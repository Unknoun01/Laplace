"""Lo que se hace con cada span antes de que salga del proceso (D-181).

Dos cosas, las dos en el proceso del usuario y antes de mandar nada:

* **Redacción de datos personales** (`init(redact=...)`). Correos, teléfonos, tarjetas,
  IBAN, IP y claves de API se cambian por una marca con su tipo y una huella corta:
  `[email:3f2a9c1d]`. La huella es la misma para el mismo valor, así que dos llamadas
  que sólo se distinguen por el correo siguen siendo distintas, y una repetición de
  verdad sigue siendo repetición. Sin la huella, todo correo sería `[email]` y dos
  peticiones de clientes distintos parecerían la misma. La huella es un HMAC: con la
  clave (`LAPLACE_REDACT_KEY`, o la API key si no hay) no se puede deshacer probando
  correos de un diccionario.
* **Muestreo por cola** (`init(sample_rate=...)`). Se decide al acabar la traza, no al
  empezar, porque al empezar no se sabe si va a fallar ni lo que va a gastar. Se queda
  siempre con la que falla, con la que pasa de un tope de tokens y con las de las
  evaluaciones y los replays. De las demás, con una de cada `1 / sample_rate`, elegida
  por el `trace_id` para que dos servicios de la misma traza decidan lo mismo. Cada
  span de una traza que se queda por azar lleva `laplace.sample.rate`, para que el
  servidor sepa a cuántas representa y lo diga.

Nada de esto puede romper al programa que observa: un fallo aquí deja pasar el span tal
cual, que es lo que había antes. Sí puede **dejar pasar menos**: si la redacción falla,
el span sale sin el contenido, porque mandar un dato personal que el usuario pidió
quitar es peor que perder un prompt.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from opentelemetry.sdk.trace import Event, ReadableSpan, SpanProcessor
from opentelemetry.trace import StatusCode
from opentelemetry.trace.status import Status

from . import semconv

logger = logging.getLogger("laplace")

# ---------------------------------------------------------------------------------
# Redacción
# ---------------------------------------------------------------------------------


def _luhn(digitos: str) -> bool:
    total, doble = 0, False
    for c in reversed(digitos):
        n = int(c)
        if doble:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
        doble = not doble
    return total % 10 == 0


def _tarjeta(texto: str) -> bool:
    digitos = re.sub(r"\D", "", texto)
    # Desde 14 cifras: un instante en milisegundos (13) no es una tarjeta.
    # Y no todas iguales: un relleno de ceros pasa Luhn y no es la tarjeta de nadie.
    return 14 <= len(digitos) <= 19 and len(set(digitos)) > 1 and _luhn(digitos)


def _iban(texto: str) -> bool:
    limpio = texto.replace(" ", "").upper()
    numero = "".join(str(int(c, 36)) for c in limpio[4:] + limpio[:4])
    return len(limpio) >= 15 and int(numero) % 97 == 1


#: Los detectores de serie: nombre, patrón y, si hace falta, una comprobación que quita
#: falsos positivos (un número de pedido de 16 cifras no es una tarjeta si no pasa Luhn).
#: Son conservadores a propósito: un `2026-10-01` o un recuento de tokens no son un
#: teléfono. Lo que no pillen se cubre con una función propia en `redact=`.
_DETECTORES: dict[str, tuple[re.Pattern[str], Callable[[str], bool] | None]] = {
    "email": (
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
        None,
    ),
    "secret": (
        re.compile(
            r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"
            r"|\bAKIA[0-9A-Z]{16}\b"
            r"|\bgh[pousr]_[A-Za-z0-9]{30,}"
            r"|\bxox[abpr]-[A-Za-z0-9-]{10,}"
            r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
            r"|(?<=Bearer )[A-Za-z0-9._~+/=-]{16,}"
        ),
        None,
    ),
    "iban": (re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"), _iban),
    "card": (re.compile(r"(?<![\d-])\d(?:[ -]?\d){13,18}(?![\d-])"), _tarjeta),
    "phone": (
        re.compile(
            # Con prefijo internacional, o en los formatos nacionales de siempre:
            # `612 345 678`, `612-345-678`, `(555) 123-4567`.
            r"(?<![\w+])\+\d{1,3}[ .-]?(?:\(\d{1,4}\)[ .-]?)?\d{2,4}(?:[ .-]?\d{2,4}){1,4}(?!\w)"
            r"|(?<![\w(])\(\d{3}\) ?\d{3}[ .-]\d{4}(?!\w)"
            r"|(?<![\w.-])\d{3}[ .-]\d{3}[ .-]\d{3,4}(?![\w.-])"
        ),
        None,
    ),
    "ip": (
        re.compile(
            r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])"
        ),
        None,
    ),
}

#: El orden importa: una clave de API puede contener algo con forma de teléfono, y un
#: IBAN tiene cifras con forma de tarjeta. Se aplica primero lo más específico.
DETECTORES = tuple(_DETECTORES)

Redaccion = bool | str | re.Pattern[str] | Callable[[str], str] | Iterable[Any] | None


#: Atributos que son estructura, no contenido: identificadores, modelos, nombres de
#: paso, tokens. No se tocan, porque son con lo que el servidor agrupa y cuenta. Los
#: identificadores del usuario (`laplace.user.id`, `laplace.customer.id`) tampoco: los
#: pone el usuario a propósito, y el margen por cliente casa por ellos con Stripe.
_ESTRUCTURA = (
    "gen_ai.operation.name",
    "gen_ai.system",
    "gen_ai.request.",
    "gen_ai.response.",
    "gen_ai.usage.",
    "gen_ai.tool.name",
    "gen_ai.tool.call.id",
    "gen_ai.agent.name",
    "gen_ai.agent.id",
    "laplace.span.type",
    "laplace.session.id",
    "laplace.user.id",
    "laplace.customer.id",
    "laplace.tags",
    "laplace.step.",
    "laplace.billing.",
    "laplace.prompt.",
    "laplace.usage.",
    "laplace.project.id",
    "laplace.sample.",
    "service.",
    "telemetry.",
    "code.",
)


def _es_estructura(clave: str) -> bool:
    return any(clave == e or (e.endswith(".") and clave.startswith(e)) for e in _ESTRUCTURA)


@dataclass
class Redactor:
    """Cambia los datos personales de un texto por su marca."""

    detectores: tuple[str, ...] = DETECTORES
    propios: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    funciones: list[Callable[[str], str]] = field(default_factory=list)
    clave: bytes = b""

    @classmethod
    def of(cls, redact: Redaccion, clave: str | None = None) -> Redactor | None:
        """`redact=True`, una lista de detectores, patrones o funciones, o una sola."""
        if redact is None or redact is False:
            return None
        clave_bytes = (clave or "").encode() or secrets.token_bytes(32)
        if redact is True:
            return cls(clave=clave_bytes)
        elementos = (
            [redact]
            if isinstance(redact, (str, re.Pattern)) or callable(redact)
            else list(redact)  # type: ignore[arg-type]
        )
        detectores: list[str] = []
        propios: list[tuple[str, re.Pattern[str]]] = []
        funciones: list[Callable[[str], str]] = []
        for e in elementos:
            if isinstance(e, str):
                if e not in _DETECTORES:
                    raise ValueError(
                        f"redact: no hay detector {e!r}; los que hay son {', '.join(DETECTORES)}"
                    )
                detectores.append(e)
            elif isinstance(e, re.Pattern):
                propios.append(("redacted", e))
            elif callable(e):
                funciones.append(e)
            else:
                raise TypeError(f"redact: no sé qué hacer con {e!r}")
        orden = tuple(d for d in DETECTORES if d in detectores)
        return cls(detectores=orden, propios=propios, funciones=funciones, clave=clave_bytes)

    def _marca(self, tipo: str, valor: str) -> str:
        huella = hmac.new(self.clave, valor.encode("utf-8"), hashlib.sha256).hexdigest()[:8]
        return f"[{tipo}:{huella}]"

    def texto(self, texto: str) -> str:
        for nombre in self.detectores:
            patron, valida = _DETECTORES[nombre]

            def cambiar(m: re.Match[str], nombre: str = nombre, valida=valida) -> str:
                if valida is not None and not valida(m.group(0)):
                    return m.group(0)
                return self._marca(nombre, m.group(0))

            texto = patron.sub(cambiar, texto)
        for nombre, patron in self.propios:
            texto = patron.sub(lambda m, n=nombre: self._marca(n, m.group(0)), texto)
        for funcion in self.funciones:
            texto = funcion(texto)
        return texto

    def valor(self, valor: Any) -> Any:
        if isinstance(valor, str):
            return self.texto(valor)
        if isinstance(valor, (list, tuple)):
            return tuple(self.valor(v) for v in valor)
        return valor

    def atributos(self, atributos: Mapping[str, Any] | None) -> dict[str, Any]:
        return {
            k: (v if _es_estructura(k) else self.valor(v)) for k, v in (atributos or {}).items()
        }


# ---------------------------------------------------------------------------------
# Muestreo
# ---------------------------------------------------------------------------------


@dataclass
class Muestreo:
    """Con qué trazas se queda el muestreo por cola."""

    #: La fracción de trazas normales que se guarda (0 < rate ≤ 1).
    rate: float = 1.0
    #: Una traza con al menos estos tokens (entrada + salida, sumando sus llamadas) se
    #: guarda siempre. El SDK no tiene la tabla de precios; los tokens son lo que se
    #: sabe en el proceso, y son lo que se cobra.
    keep_tokens: int | None = 20_000
    #: Y una que dura al menos esto, en milisegundos. `None` no lo mira.
    keep_ms: float | None = None

    def por_azar(self, trace_id: int) -> bool:
        """Determinista por `trace_id`, como `TraceIdRatioBased` de OpenTelemetry: dos
        servicios que ven la misma traza deciden lo mismo y no quedan trazas a medias."""
        return (trace_id & 0xFFFF_FFFF_FFFF_FFFF) < int(self.rate * 2**64)


_ETIQUETAS_SIEMPRE = (semconv.EVAL_TAG, "laplace-replay")


@dataclass
class _Traza:
    spans: list[ReadableSpan] = field(default_factory=list)
    tokens: int = 0
    error: bool = False
    siempre: bool = False
    inicio: int | None = None
    fin: int | None = None
    visto: float = field(default_factory=time.monotonic)


def _tokens(span: ReadableSpan) -> int:
    atributos = span.attributes or {}
    total = 0
    for clave in (semconv.GEN_AI_USAGE_INPUT_TOKENS, semconv.GEN_AI_USAGE_OUTPUT_TOKENS):
        try:
            total += int(atributos.get(clave) or 0)
        except (TypeError, ValueError):
            pass
    return total


def _con_error(span: ReadableSpan) -> bool:
    if span.status is not None and span.status.status_code is StatusCode.ERROR:
        return True
    return any(e.name == semconv.EVENT_EXCEPTION for e in span.events or ())


def _siempre(span: ReadableSpan) -> bool:
    etiquetas = str((span.attributes or {}).get(semconv.LAPLACE_TAGS) or "")
    return any(e in etiquetas for e in _ETIQUETAS_SIEMPRE)


# ---------------------------------------------------------------------------------
# El procesador
# ---------------------------------------------------------------------------------


def _copia(span: ReadableSpan, redactor: Redactor | None, extra: Mapping[str, Any]) -> ReadableSpan:
    """El mismo span con los atributos redactados y `extra` añadido. Un span terminado
    no se puede cambiar, así que se hace uno nuevo con todo lo demás igual."""
    atributos = dict(span.attributes or {})
    eventos = list(span.events or ())
    estado = span.status
    if redactor is not None:
        atributos = redactor.atributos(atributos)
        eventos = [
            Event(e.name, redactor.atributos(e.attributes), timestamp=e.timestamp) for e in eventos
        ]
        if estado is not None and estado.description:
            estado = Status(estado.status_code, redactor.texto(estado.description))
    atributos.update(extra)
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=atributos,
        events=eventos,
        links=span.links,
        kind=span.kind,
        status=estado,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


def _sin_contenido(span: ReadableSpan) -> ReadableSpan:
    """Si la redacción falla, el span sale sin lo que no es estructura."""
    atributos = {k: v for k, v in (span.attributes or {}).items() if _es_estructura(k)}
    atributos["laplace.redaction.failed"] = True
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=atributos,
        links=span.links,
        kind=span.kind,
        status=Status(span.status.status_code if span.status is not None else StatusCode.UNSET),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class FiltroProcessor(SpanProcessor):
    """Redacta y muestrea antes de pasarle los spans al procesador que exporta.

    Las trazas se guardan en memoria hasta que acaba su raíz *local* (un span sin padre,
    o con el padre en otro proceso). Para que eso no crezca sin límite: una traza que
    lleva `max_wait_s` sin cerrarse, o que pasa de `max_spans`, o cuando hay más de
    `max_traces` abiertas, se manda entera, sin muestrear. Ante la duda se guarda, que
    es lo que había antes del muestreo.
    """

    def __init__(
        self,
        inner: SpanProcessor,
        *,
        redactor: Redactor | None = None,
        muestreo: Muestreo | None = None,
        max_traces: int = 2_000,
        max_spans: int = 5_000,
        max_wait_s: float = 300.0,
        recordadas: int = 10_000,
    ) -> None:
        self.inner = inner
        self.redactor = redactor
        self.muestreo = muestreo if muestreo is not None and muestreo.rate < 1 else None
        self.max_traces = max_traces
        self.max_spans = max_spans
        self.max_wait_s = max_wait_s
        self._abiertas: OrderedDict[int, _Traza] = OrderedDict()
        # Lo decidido de las trazas ya cerradas: un span que acaba después de su raíz
        # (una tarea en segundo plano) sigue la misma suerte que el resto.
        self._decididas: OrderedDict[int, dict[str, Any] | None] = OrderedDict()
        self._recordadas = recordadas
        self._cerrojo = threading.Lock()
        #: Cuántas trazas se han quedado fuera, para quien quiera mirarlo.
        self.descartadas = 0

    # -- la interfaz de SpanProcessor ----------------------------------------------

    def on_start(self, span, parent_context=None) -> None:
        self.inner.on_start(span, parent_context=parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        try:
            listos = self._recibir(span)
        except Exception:  # noqa: BLE001 - observar no puede romper lo observado
            logger.debug("laplace: fallo en el muestreo; el span pasa tal cual", exc_info=True)
            listos = [(span, {})]
        for s, extra in listos:
            self.inner.on_end(self._limpio(s, extra))

    def shutdown(self) -> None:
        self._vaciar()
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        self._vaciar()
        return self.inner.force_flush(timeout_millis)

    # -- dentro --------------------------------------------------------------------

    def _limpio(self, span: ReadableSpan, extra: Mapping[str, Any]) -> ReadableSpan:
        if self.redactor is None and not extra:
            return span
        try:
            return _copia(span, self.redactor, extra)
        except Exception:  # noqa: BLE001
            logger.warning("laplace: no se pudo redactar un span; sale sin su contenido")
            return _sin_contenido(span)

    def _recibir(self, span: ReadableSpan) -> list[tuple[ReadableSpan, Mapping[str, Any]]]:
        if self.muestreo is None or span.context is None:
            return [(span, {})]
        tid = span.context.trace_id
        with self._cerrojo:
            if tid in self._decididas:
                extra = self._decididas[tid]
                return [] if extra is None else [(span, extra)]
            traza = self._abiertas.get(tid)
            if traza is None:
                traza = self._abiertas[tid] = _Traza()
            traza.spans.append(span)
            traza.tokens += _tokens(span)
            traza.error = traza.error or _con_error(span)
            traza.siempre = traza.siempre or _siempre(span)
            if span.start_time is not None:
                traza.inicio = min(traza.inicio or span.start_time, span.start_time)
            if span.end_time is not None:
                traza.fin = max(traza.fin or span.end_time, span.end_time)
            raiz = span.parent is None or span.parent.is_remote
            listos: list[tuple[ReadableSpan, Mapping[str, Any]]] = []
            if raiz:
                listos += self._decidir(tid)
            elif len(traza.spans) > self.max_spans:
                listos += self._soltar(tid)
            listos += self._caducadas()
            return listos

    def _decidir(self, tid: int) -> list[tuple[ReadableSpan, Mapping[str, Any]]]:
        assert self.muestreo is not None
        traza = self._abiertas.pop(tid)
        m = self.muestreo
        duracion_ms = ((traza.fin or 0) - (traza.inicio or 0)) / 1e6
        if (
            traza.error
            or traza.siempre
            or (m.keep_tokens is not None and traza.tokens >= m.keep_tokens)
            or (m.keep_ms is not None and duracion_ms >= m.keep_ms)
        ):
            extra: dict[str, Any] | None = {}
        elif m.por_azar(tid):
            extra = {"laplace.sample.rate": round(1 / m.rate, 6)}
        else:
            extra = None
            self.descartadas += 1
        self._recordar(tid, extra)
        return [] if extra is None else [(s, extra) for s in traza.spans]

    def _soltar(self, tid: int) -> list[tuple[ReadableSpan, Mapping[str, Any]]]:
        """Manda una traza entera sin muestrear, y lo que llegue después también."""
        traza = self._abiertas.pop(tid)
        self._recordar(tid, {})
        return [(s, {}) for s in traza.spans]

    def _recordar(self, tid: int, extra: dict[str, Any] | None) -> None:
        self._decididas[tid] = extra
        while len(self._decididas) > self._recordadas:
            self._decididas.popitem(last=False)

    def _caducadas(self) -> list[tuple[ReadableSpan, Mapping[str, Any]]]:
        ahora = time.monotonic()
        listos: list[tuple[ReadableSpan, Mapping[str, Any]]] = []
        for tid in list(self._abiertas):
            traza = self._abiertas[tid]
            if len(self._abiertas) > self.max_traces or ahora - traza.visto > self.max_wait_s:
                listos += self._soltar(tid)
            else:
                break  # en orden de llegada: las demás son más nuevas
        return listos

    def _vaciar(self) -> None:
        with self._cerrojo:
            listos = [x for tid in list(self._abiertas) for x in self._soltar(tid)]
        for s, extra in listos:
            self.inner.on_end(self._limpio(s, extra))


def clave_de_redaccion(api_key: str | None) -> str | None:
    """La clave de las huellas: `LAPLACE_REDACT_KEY`, o la API key. Sin ninguna, una al
    azar por proceso, y las huellas sólo casan dentro del mismo proceso."""
    return os.getenv("LAPLACE_REDACT_KEY") or api_key or None
