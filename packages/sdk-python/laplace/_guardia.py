"""`laplace.guard`: cortar una ejecución que gasta de más o da vueltas sin avanzar.

    with laplace.guard(max_usd_per_run=0.50, max_loop=5):
        agente.run(pregunta)

    @laplace.guard(max_usd_per_run=0.50)
    def atender(pregunta): ...

Laplace dice después qué agente se fue de presupuesto o se quedó en un bucle; esto lo
para mientras pasa. Se comprueba **antes** de cada llamada al modelo y de cada paso
decorado con `@observe`, y si se ha pasado un límite la llamada no se hace: se lanza
`GuardExceeded`, que sube por el agente como cualquier excepción y queda en la traza
como el error del paso que la recibió.

Dos límites, los dos opcionales:

* `max_usd_per_run`: el gasto de las llamadas al modelo dentro del bloque, con la misma
  tabla y la misma cuenta que usa Laplace para el coste (`laplace.pricing`). La llamada
  que cruza el límite ya está pagada cuando se sabe lo que costó; se corta la
  siguiente. Una llamada sin recuento del proveedor cuenta lo estimado por el texto,
  como en la traza.
* `max_loop`: cuántas veces puede repetirse el mismo paso con la misma entrada —sin
  mirar los números— **sin avanzar**: con dos salidas distintas como mucho, y alguna
  repetida (dos vueltas con dos salidas distintas todavía pueden ser avance).
  Es la misma definición de bucle que la regla del Diagnóstico (D-109): un paso que
  procesa seis pedidos distintos produce seis salidas distintas y no se corta.

Un modelo sin tarifa no cuesta cero (contrato): su gasto no se puede sumar, y en vez de
contarlo como gratis se dice. Queda en `unknown_cost_models`, `cost_complete` pasa a
False y se avisa una vez por el log. Para darle tarifa: `LAPLACE_PRICES_EXTRA`.

Lo que se pone en Laplace (Ajustes: tope por ejecución, de bucles y la parada) se suma
a lo del código y manda el más estricto (`_control.py`). Sin `guard` en el código, la
ejecución es la raíz de `@observe` (o de `laplace.span`); la parada corta cualquier
llamada, esté donde esté.

Lo que no ve: llamadas hechas por otra instrumentación (OpenInference, OpenLLMetry) en
lugar de por las integraciones de Laplace, y lo que corra en otro hilo sin copiar el
contexto (`contextvars.copy_context()`); los límites viven en el contexto, como la traza.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
import re
import threading
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, TypeVar

from . import semconv

logger = logging.getLogger("laplace")

F = TypeVar("F", bound=Callable[..., Any])

#: Salidas distintas como mucho para que repetir sea «no avanzar». El mismo número que
#: `MAX_SALIDAS_BUCLE` del Diagnóstico: un límite que cortase lo que la regla no señala
#: (o al revés) haría que el producto se contradijese a sí mismo.
MAX_SALIDAS_BUCLE = 2

MOTIVO_GASTO = "max_usd_per_run"
MOTIVO_BUCLE = "max_loop"
MOTIVO_PARADA = "stopped"

#: De dónde salió el límite que cortó: del código o de lo puesto en Laplace.
ORIGEN_CODIGO = "code"
ORIGEN_LAPLACE = "laplace"


class GuardExceeded(Exception):  # noqa: N818 - el nombre es la API pública
    """Se ha pasado un límite de `laplace.guard` y la llamada siguiente no se ha hecho."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        limit: float,
        spent_usd: float,
        step: str = "",
        repeats: int = 0,
        source: str = ORIGEN_CODIGO,
    ) -> None:
        super().__init__(message)
        #: `max_usd_per_run` o `max_loop`.
        self.reason = reason
        self.limit = limit
        self.spent_usd = spent_usd
        #: El paso que se iba a repetir, si el corte es por bucle.
        self.step = step
        self.repeats = repeats
        #: `code` (el `guard` del código) o `laplace` (lo puesto en Ajustes).
        self.source = source


# ---------------------------------------------------------------------------------
# Huellas: las mismas que calcula la ingesta para la regla de bucles
# ---------------------------------------------------------------------------------

_SOLO_DIGITOS = re.compile(r"\d+")
_ESPACIOS = re.compile(r"\s+")


def huella(tipo: str, nombre: str, valor: Any, *, ignorar_numeros: bool) -> str:
    """Gemela de `loop_hash` de la ingesta (`ingest/otlp.py`), y una prueba lo vigila.

    En la entrada los números se borran (el contador de intentos no cambia la pregunta);
    en la salida se dejan, porque ahí son el avance (D-109).
    """
    if isinstance(valor, (dict, list)):
        texto = json.dumps(valor, sort_keys=True, ensure_ascii=False, default=str)
    else:
        texto = "" if valor is None else str(valor)
    if ignorar_numeros:
        texto = _SOLO_DIGITOS.sub("#", texto)
    texto = _ESPACIOS.sub(" ", texto.lower()).strip()
    material = "|".join([tipo, nombre, texto])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------------
# El estado de una ejecución
# ---------------------------------------------------------------------------------


@dataclass
class _Paso:
    nombre: str
    veces: int = 0
    salidas: set[str] = field(default_factory=set)


@dataclass
class Vuelta:
    """Una llamada ya autorizada, para apuntarle la salida y el coste cuando acabe."""

    guardias: tuple[_Ejecucion, ...]
    clave: str
    tipo: str
    nombre: str


class _Ejecucion:
    def __init__(self, max_usd: float | None, max_loop: int | None) -> None:
        self.max_usd = max_usd
        self.max_loop = max_loop
        self._lock = threading.Lock()
        self._pasos: dict[str, _Paso] = {}
        #: Spans de modelo todavía sin cerrar (un stream a medio consumir): se cobran
        #: cuando terminan, no antes, porque hasta entonces no hay recuento.
        self._abiertos: list[Any] = []
        self._gastado = 0.0
        self.sin_tarifa: set[str] = set()
        self.cortada: GuardExceeded | None = None

    # -- coste ---------------------------------------------------------------------

    def _cobrar_cerrados(self) -> None:
        pendientes = []
        for span in self._abiertos:
            if getattr(span, "end_time", None) is None:
                pendientes.append(span)
                continue
            self._cobrar(getattr(span, "attributes", None) or {})
        self._abiertos = pendientes

    def _cobrar(self, attrs: Any) -> None:
        from .pricing import get_price_table

        modelo = attrs.get(semconv.GEN_AI_RESPONSE_MODEL) or attrs.get(semconv.GEN_AI_REQUEST_MODEL)
        entrada = int(attrs.get(semconv.GEN_AI_USAGE_INPUT_TOKENS) or 0)
        salida = int(attrs.get(semconv.GEN_AI_USAGE_OUTPUT_TOKENS) or 0)
        if not entrada and not salida:
            # Sin respuesta (un error de conexión) no hubo nada que cobrar (D-183).
            return
        coste = get_price_table().compute(
            str(modelo or ""),
            input_tokens=entrada,
            output_tokens=salida,
            cached_input_tokens=int(attrs.get(semconv.LAPLACE_USAGE_CACHED_INPUT_TOKENS) or 0),
            cache_write_tokens=int(attrs.get(semconv.LAPLACE_USAGE_CACHE_WRITE_TOKENS) or 0),
            cache_write_1h_tokens=int(attrs.get(semconv.LAPLACE_USAGE_CACHE_WRITE_1H_TOKENS) or 0),
            tier=str(attrs.get(semconv.LAPLACE_BILLING_TIER) or "standard"),
            region=str(attrs.get(semconv.LAPLACE_BILLING_REGION) or "global"),
        )
        if coste.unknown:
            nombre = str(modelo or "(sin modelo)")
            if nombre not in self.sin_tarifa:
                logger.warning(
                    "laplace.guard: «%s» no tiene tarifa, así que su gasto no cuenta para "
                    "max_usd_per_run. Dale una con LAPLACE_PRICES_EXTRA.",
                    nombre,
                )
            self.sin_tarifa.add(nombre)
            return
        self._gastado += coste.total_usd

    @property
    def gastado(self) -> float:
        with self._lock:
            self._cobrar_cerrados()
            return self._gastado

    # -- comprobar -----------------------------------------------------------------

    def comprobar(self, clave: str, nombre: str, remotas: Any) -> None:
        """Lanza `GuardExceeded` si la llamada que viene no se debe hacer.

        Los límites se leen en cada comprobación y no al abrir el bloque: un tope puesto
        en Laplace a mitad de una ejecución larga la alcanza en la siguiente llamada.
        """
        with self._lock:
            if self.cortada is not None:
                # Cortada una vez, cortada para el resto del bloque: un agente que
                # captura la excepción y sigue no puede volver a gastar.
                raise self.cortada
            self._cobrar_cerrados()
            max_usd, origen_usd = mas_estricto(self.max_usd, remotas.max_usd_per_run)
            if max_usd is not None and self._gastado >= max_usd:
                self.cortada = GuardExceeded(
                    f"laplace.guard: la ejecución lleva gastados {self._gastado:.4f} USD y "
                    f"el límite es {max_usd:.4f} USD{_puesto(origen_usd)}; la llamada "
                    f"siguiente no se hace.",
                    reason=MOTIVO_GASTO,
                    limit=max_usd,
                    spent_usd=self._gastado,
                    source=origen_usd,
                )
                raise self.cortada
            max_loop, origen_bucle = mas_estricto(self.max_loop, remotas.max_loop)
            paso = self._pasos.get(clave)
            if (
                max_loop is not None
                and paso is not None
                and paso.veces >= max_loop
                and len(paso.salidas) <= min(MAX_SALIDAS_BUCLE, paso.veces - 1)
            ):
                self.cortada = GuardExceeded(
                    f"laplace.guard: «{nombre}» va a repetirse por {paso.veces + 1}ª vez con "
                    f"la misma entrada y sin avanzar ({len(paso.salidas)} salidas distintas); "
                    f"el límite es {max_loop}{_puesto(origen_bucle)}.",
                    reason=MOTIVO_BUCLE,
                    limit=float(max_loop),
                    spent_usd=self._gastado,
                    step=nombre,
                    repeats=paso.veces,
                    source=origen_bucle,
                )
                raise self.cortada

    def cortar(self, exc: GuardExceeded) -> None:
        with self._lock:
            if self.cortada is None:
                self.cortada = exc

    def apuntar(self, clave: str, nombre: str) -> None:
        with self._lock:
            self._pasos.setdefault(clave, _Paso(nombre)).veces += 1

    def salida(self, clave: str, huella_salida: str) -> None:
        with self._lock:
            paso = self._pasos.get(clave)
            if paso is not None:
                paso.salidas.add(huella_salida)

    def span_de_modelo(self, span: Any) -> None:
        with self._lock:
            self._abiertos.append(span)


_activas: ContextVar[tuple[_Ejecucion, ...]] = ContextVar("laplace_guard", default=())


def mas_estricto(codigo: Any, laplace: Any) -> tuple[Any, str]:
    """El límite que manda y de dónde sale. Con los dos iguales, el del código."""
    if laplace is None or (codigo is not None and codigo <= laplace):
        return codigo, ORIGEN_CODIGO
    return laplace, ORIGEN_LAPLACE


def _puesto(origen: str) -> str:
    return ", puesto en Laplace" if origen == ORIGEN_LAPLACE else ""


def _remotas() -> Any:
    from ._control import actuales

    return actuales()


# ---------------------------------------------------------------------------------
# Lo que llaman las integraciones y `@observe`
# ---------------------------------------------------------------------------------


def hay_alguno() -> bool:
    return bool(_activas.get()) or _remotas().stopped


def antes(tipo: str, nombre: str, entrada: Any) -> Vuelta | None:
    """Antes de una llamada al modelo o de un paso. Lanza si no se debe hacer.

    Devuelve con qué apuntarle la salida (`despues`) y, si es del modelo, el span
    (`con_span`). Sin ningún `guard` abierto ni parada no hace nada ni cuesta nada.
    """
    remotas = _remotas()
    guardias = _activas.get()
    if remotas.stopped:
        parada = GuardExceeded(
            f"laplace.guard: el proyecto está parado desde Laplace; «{nombre}» no se hace.",
            reason=MOTIVO_PARADA,
            limit=0.0,
            spent_usd=guardias[-1].gastado if guardias else 0.0,
            step=nombre,
            source=ORIGEN_LAPLACE,
        )
        # Las ejecuciones en marcha quedan cortadas: reanudar deja empezar otras, no
        # resucita una que se paró a medias.
        for guardia in guardias:
            guardia.cortar(parada)
        raise parada
    if not guardias:
        return None
    clave = huella(tipo, nombre, entrada, ignorar_numeros=True)
    for guardia in guardias:
        guardia.comprobar(clave, nombre, remotas)
    for guardia in guardias:
        guardia.apuntar(clave, nombre)
    return Vuelta(guardias, clave, tipo, nombre)


def con_span(vuelta: Vuelta | None, span: Any) -> None:
    if vuelta is None:
        return
    for guardia in vuelta.guardias:
        guardia.span_de_modelo(span)


def despues(vuelta: Vuelta | None, salida: Any) -> None:
    if vuelta is None:
        return
    h = huella(vuelta.tipo, vuelta.nombre, salida, ignorar_numeros=False)
    for guardia in vuelta.guardias:
        guardia.salida(vuelta.clave, h)


#: La vuelta de cada span de modelo abierto, para apuntarle la salida desde
#: `record_response`, que sólo recibe el span. Débil: un span que falla y no llega a
#: tener respuesta no se queda aquí para siempre.
_vueltas_por_span: weakref.WeakKeyDictionary[Any, Vuelta] = weakref.WeakKeyDictionary()
_vueltas_lock = threading.Lock()


def vincular(span: Any, vuelta: Vuelta | None) -> None:
    if vuelta is None:
        return
    con_span(vuelta, span)
    try:
        with _vueltas_lock:
            _vueltas_por_span[span] = vuelta
    except TypeError:  # un span sin referencias débiles: sólo se pierde la salida
        logger.debug("laplace.guard: span sin referencias débiles", exc_info=True)


def salida_de_span(span: Any, salida: Any) -> None:
    try:
        with _vueltas_lock:
            vuelta = _vueltas_por_span.pop(span, None)
    except TypeError:
        return
    despues(vuelta, salida)


@contextmanager
def implicita(raiz: bool) -> Iterator[None]:
    """La ejecución de quien no ha escrito `guard`: la raíz de `@observe`.

    Sólo si Laplace tiene algún límite puesto, y sólo en la raíz y sin otro `guard`
    abierto: con uno, ése ya aplica lo de Laplace. Sin límites no se abre nada, para no
    pagar huellas en cada paso de quien no las necesita.
    """
    if not raiz or _activas.get() or not _remotas().limita:
        yield
        return
    token = _activas.set((_Ejecucion(None, None),))
    try:
        yield
    finally:
        _activas.reset(token)


# ---------------------------------------------------------------------------------
# La API
# ---------------------------------------------------------------------------------


class guard:  # noqa: N801 - se usa como función: `laplace.guard(...)`
    """Límites para una ejecución del agente. Bloque `with` (también `async with`) o
    decorador; como decorador, cada llamada a la función es una ejecución nueva."""

    def __init__(
        self, *, max_usd_per_run: float | None = None, max_loop: int | None = None
    ) -> None:
        if max_usd_per_run is None and max_loop is None:
            raise ValueError("laplace.guard: pon max_usd_per_run, max_loop o los dos")
        if max_usd_per_run is not None and max_usd_per_run <= 0:
            raise ValueError("laplace.guard: max_usd_per_run tiene que ser mayor que cero")
        if max_loop is not None and max_loop < 2:
            # Con una sola vuelta no hay con qué comparar: no se sabe si avanza.
            raise ValueError("laplace.guard: max_loop tiene que ser 2 o más")
        self.max_usd_per_run = max_usd_per_run
        self.max_loop = max_loop
        self._ejecucion: _Ejecucion | None = None
        self._token: Any = None

    # -- lo que se puede leer al acabar ----------------------------------------------

    @property
    def spent_usd(self) -> float:
        """Lo gastado en llamadas con tarifa. Si `cost_complete` es False, hay más."""
        return self._ejecucion.gastado if self._ejecucion else 0.0

    @property
    def unknown_cost_models(self) -> list[str]:
        return sorted(self._ejecucion.sin_tarifa) if self._ejecucion else []

    @property
    def cost_complete(self) -> bool:
        return not self.unknown_cost_models

    @property
    def exceeded(self) -> GuardExceeded | None:
        return self._ejecucion.cortada if self._ejecucion else None

    # -- bloque ----------------------------------------------------------------------

    def __enter__(self) -> guard:
        if self._token is not None:
            raise RuntimeError("laplace.guard: este bloque ya está abierto")
        self._ejecucion = _Ejecucion(self.max_usd_per_run, self.max_loop)
        self._token = _activas.set((*_activas.get(), self._ejecucion))
        return self

    def __exit__(self, *exc: Any) -> None:
        _activas.reset(self._token)
        self._token = None

    async def __aenter__(self) -> guard:
        return self.__enter__()

    async def __aexit__(self, *exc: Any) -> None:
        self.__exit__(*exc)

    # -- decorador -------------------------------------------------------------------

    def _nuevo(self) -> guard:
        return guard(max_usd_per_run=self.max_usd_per_run, max_loop=self.max_loop)

    def __call__(self, fn: F) -> F:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                async with self._nuevo():
                    return await fn(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with self._nuevo():
                return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]
