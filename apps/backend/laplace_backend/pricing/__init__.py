"""Cálculo de coste por span.

El coste se calcula aquí, en la ingesta, nunca en el SDK: los precios cambian y no
pueden quedar congelados en la versión que el usuario tenga instalada (D-005).

Cuatro reglas que sostienen la credibilidad de todo el producto:

1. **Un modelo sin tarifa no cuesta cero, cuesta "no lo sabemos".** Un 0 silencioso se
   suma a los totales y los corrompe sin que nadie se entere. El span se marca como
   coste desconocido y la interfaz lo dice.
2. **Resolver por prefijo no puede saltar de versión.** `claude-opus-4-6` no puede
   heredar la tarifa de `claude-opus-4` (que es el triple). Sólo se acepta el prefijo
   cuando lo que sobra es un sufijo de snapshot o una palabra, nunca otro número de
   versión.
3. **La entrada no se cobra a una sola tarifa.** Un agente repite su prompt de sistema
   en cada paso, así que el caché salta siempre: cobrar el 100 % de esa entrada infla
   la factura del usuario y, con ella, el ahorro que le prometemos. Se cobra cada tramo
   de tokens con el metro que le corresponde (D-050).
4. **Ante la duda, la tarifa que menos ahorro produce.** Cuando no se puede saber qué
   metro aplicó —contexto largo, residencia de datos, modo rápido—, se cobra el
   estándar y el span queda marcado como tarifa asumida, visible en modo avanzado.
   Nunca se elige en silencio la tarifa que engorda nuestro número (D-051).

Y dos capas (D-138). La propia, transcrita de la página de cada proveedor y con fecha, y
debajo la tabla comunitaria de LiteLLM, que cubre miles de modelos más. La propia
resuelve primero, también por snapshot; lo que sale sólo de LiteLLM se cobra, pero
marcado como tarifa no verificada.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("laplace.pricing")

_PRICES_PATH = Path(__file__).with_name("model_prices.json")
#: La capa no verificada: la tabla de LiteLLM ya convertida a nuestro formato. La
#: regenera `scripts/precios_litellm.py`, y un trabajo semanal de CI abre la PR.
LITELLM_PATH = Path(__file__).with_name("litellm_prices.json")
#: Cómo se reconoce en `rate` una tarifa de la capa no verificada. Es lo único que se
#: guarda de ella, así que los dos almacenes derivan la marca de aquí al leer.
MARCA_NO_VERIFICADA = " @ litellm "


def es_no_verificada(rate: str | None) -> bool:
    return MARCA_NO_VERIFICADA in (rate or "")


def modelos_no_verificados(models: Any) -> list[str]:
    """De esos modelos, los que se cobran con la capa de LiteLLM.

    Para lo agregado —un hallazgo, una traza— se decide con la tabla en vigor y no
    span a span: la marca va por modelo, y así no hace falta una columna más en los dos
    almacenes (D-141). Si la tabla cambia, lo guardado se recalcula (D-123).
    """
    tabla = get_price_table()
    salida = set()
    for modelo in models or ():
        precio = tabla.lookup(modelo) if modelo else None
        if precio is not None and not precio.verified:
            salida.add(str(modelo))
    return sorted(salida)
_MILLION = 1_000_000.0

#: Metro de facturación pedido por quien hizo la llamada.
BillingTier = Literal["standard", "batch", "fast"]
#: Enrutado de la petición. `regional` = residencia de datos, que lleva recargo.
BillingRegion = Literal["global", "regional"]

#: Lo que puede sobrar tras un prefijo para seguir siendo el mismo modelo: un snapshot
#: con fecha (`-20250929`, `-2024-07-18`), una revisión (`-v1`) o una de las pocas
#: palabras que los proveedores usan para decir «el mismo modelo».
#:
#: La lista de palabras es cerrada a propósito. Aceptar cualquier palabra dejaba pasar
#: `-pro`, `-mini`, `-nano` y `-cyber`, que no son snapshots sino modelos distintos con
#: precios muy distintos: `gpt-5.5-pro` cuesta seis veces `gpt-5.5`, y un futuro
#: `gpt-5.6-terra-mini` habría heredado en silencio la tarifa de `gpt-5.6-terra`. Con
#: la lista cerrada, un sufijo desconocido deja el modelo como «coste desconocido», que
#: es el lado por el que hay que equivocarse.
_SNAPSHOT_SUFFIX = re.compile(r"^-(\d{8}|\d{4}-\d{2}-\d{2}|v\d+|latest|preview|beta|stable|exp)$")


def _candidates(model: str) -> list[str]:
    """Formas del identificador con las que probar, de la más literal a la más pelada.

    Los gateways adornan el nombre: `openai/gpt-5.6-luna`, `anthropic.claude-sonnet-5`,
    `bedrock/anthropic.claude-sonnet-5-v1:0`. Se quitan esos adornos con cuidado de no
    partir un número de versión: en `gpt-5.1` el punto es parte del modelo, no un
    separador de proveedor, así que sólo se corta por `.` si lo que queda detrás no
    empieza por un dígito.
    """
    found = [model]
    if ":" in model:
        found.append(model.split(":", 1)[0])
    for value in list(found):
        if "/" in value:
            found.append(value.rsplit("/", 1)[-1])
    for value in list(found):
        if "." in value:
            tail = value.rsplit(".", 1)[-1]
            if tail and not tail[0].isdigit():
                found.append(tail)
    # Sin duplicados y conservando el orden.
    return list(dict.fromkeys(found))


@dataclass(frozen=True)
class ModelPrice:
    """Precio en USD por 1M de tokens, con todos sus metros.

    Un campo a `None` significa "el proveedor no publica ese metro para este modelo",
    que no es lo mismo que valer cero: por ejemplo, un modelo sin `cache_write` no
    cobra aparte por escribir en caché, y esos tokens van a la tarifa de entrada.
    """

    model: str
    input: float
    output: float
    #: Lectura de caché (cache hit).
    cached_input: float | None = None
    #: Escritura de caché de duración corta (5 min en Anthropic, automática en OpenAI).
    cache_write: float | None = None
    #: Escritura de caché de larga duración (1 h en Anthropic).
    cache_write_1h: float | None = None

    #: Tramo de contexto largo. Se guarda pero **no se aplica solo**: ver `compute`.
    long_context_input: float | None = None
    long_context_output: float | None = None
    long_context_cached_input: float | None = None
    long_context_cache_write: float | None = None

    #: Modo rápido, cuando el proveedor publica tarifa propia para este modelo.
    fast_input: float | None = None
    fast_output: float | None = None

    #: True si el modelo admite el recargo por residencia de datos.
    region_surcharge: bool = False
    #: Fecha en la que caduca una tarifa promocional, si la tiene.
    expires: str | None = None

    #: Modelo más barato del mismo proveedor que proponer para tareas cortas.
    alternative: str | None = None
    #: Clave del bloque `sources`: de dónde salen estos números.
    source: str = ""
    note: str = ""
    #: False si la tarifa sale sólo de la tabla de LiteLLM: se cobra, pero se dice.
    verified: bool = True


@dataclass(frozen=True)
class Source:
    """Una página de precios, con las reglas uniformes que publica."""

    url: str
    verified_at: str
    #: Descuento del API de lotes, publicado como regla uniforme (0,5 = 50 %).
    batch_multiplier: float | None = None
    #: Recargo por residencia de datos (1,1 = 10 % adicional).
    region_multiplier: float | None = None
    #: Multiplicador del modo rápido cuando el proveedor no publica tarifa por modelo.
    fast_multiplier: float | None = None
    #: Tokens de entrada a partir de los cuales el tramo de contexto largo *podría*
    #: aplicar. No se usa para cobrar: sólo para marcar el coste como asumido.
    long_context_flag_tokens: int | None = None
    note: str = ""


@dataclass(frozen=True)
class CostBreakdown:
    """Coste de un span, desglosado por metro."""

    input_usd: float = 0.0
    output_usd: float = 0.0
    total_usd: float = 0.0
    #: Parte de `input_usd` que se ha ido en leer y escribir caché.
    cache_read_usd: float = 0.0
    cache_write_usd: float = 0.0
    #: Lo que la caché ya ha ahorrado en este span, frente a pagar esa entrada entera
    #: a tarifa estándar. Es dinero medido, no una promesa.
    cache_saving_usd: float = 0.0

    #: True cuando no hay tarifa para ese modelo. El coste NO es cero: es desconocido.
    unknown: bool = True
    #: True cuando no se ha podido saber qué metro aplicó y se ha cobrado el estándar.
    assumed: bool = False
    #: True cuando la tarifa sale sólo de LiteLLM. No es un suelo como `assumed`: puede
    #: quedarse corta o pasarse, y lo único honrado es decir que no está verificada.
    unverified: bool = False
    #: Tarifa aplicada, para poder auditarla en modo avanzado.
    #: Formato: `<modelo de la tabla> @ <versión de la tabla>`.
    rate: str = ""
    #: Por qué la tarifa es una suposición, cuando lo es.
    note: str = ""


class PriceTable:
    """Tabla de precios por modelo, cargada del JSON del repo."""

    def __init__(
        self,
        models: dict[str, ModelPrice],
        version: str,
        sources: dict[str, Source],
        unverified: dict[str, ModelPrice] | None = None,
        unverified_date: str = "",
    ) -> None:
        self._models = models
        self._version = version
        self._sources = sources
        self._unverified = unverified or {}
        self._unverified_date = unverified_date
        # Prefijos de más largo a más corto: la coincidencia más específica gana.
        self._prefixes = sorted(models, key=len, reverse=True)
        self._unverified_prefixes = sorted(self._unverified, key=len, reverse=True)
        # Cada span pregunta por su modelo, y con la capa de LiteLLM son miles de
        # prefijos que recorrer: la respuesta se recuerda mientras viva esta tabla.
        self._memo: dict[str, ModelPrice | None] = {}

    @classmethod
    def load(
        cls, path: Path | None = None, *, unverified_path: Path | None = None
    ) -> PriceTable:
        # Las tarifas propias y la capa de LiteLLM sólo se aplican a la tabla del
        # producto: una prueba que carga su propio fichero tiene que leer exactamente
        # ese fichero, salvo que pida la capa a propósito.
        propias = path is None
        path = path or _PRICES_PATH
        if unverified_path is None and propias:
            unverified_path = LITELLM_PATH
        unverified, unverified_date = _load_unverified(unverified_path)
        try:
            raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("no se pudo leer la tabla de precios en %s", path)
            return cls({}, "desconocida", {})

        # Las tarifas propias: un modelo que no está en la tabla, o uno con precio
        # negociado. Sin esto, la única salida para quien instala con pip era editar
        # un fichero dentro de su site-packages.
        raw_models: dict[str, Any] = dict(raw.get("models") or {})
        extra = os.environ.get("LAPLACE_PRICES_EXTRA", "").strip() if propias else ""
        if extra:
            try:
                propio = json.loads(Path(extra).expanduser().read_text(encoding="utf-8"))
                raw_models.update(propio.get("models") or {})
            except Exception:  # noqa: BLE001
                logger.exception("no se pudo leer LAPLACE_PRICES_EXTRA en %s", extra)
        # Y las que se ponen desde la interfaz, que mandan sobre todo lo anterior: son
        # lo último que alguien ha dicho a propósito sobre ese modelo (D-123).
        if propias:
            raw_models.update(_custom)

        models = {
            name: ModelPrice(
                model=name,
                input=float(entry.get("input", 0.0)),
                output=float(entry.get("output", 0.0)),
                cached_input=_opt(entry.get("cached_input")),
                cache_write=_opt(entry.get("cache_write")),
                cache_write_1h=_opt(entry.get("cache_write_1h")),
                long_context_input=_opt(entry.get("long_context_input")),
                long_context_output=_opt(entry.get("long_context_output")),
                long_context_cached_input=_opt(entry.get("long_context_cached_input")),
                long_context_cache_write=_opt(entry.get("long_context_cache_write")),
                fast_input=_opt(entry.get("fast_input")),
                fast_output=_opt(entry.get("fast_output")),
                region_surcharge=bool(entry.get("region_surcharge", False)),
                expires=entry.get("expires") or None,
                alternative=entry.get("alternative") or None,
                source=entry.get("source", ""),
                note=entry.get("note", ""),
            )
            for name, entry in raw_models.items()
        }
        sources = {
            key: Source(
                url=value.get("url", ""),
                verified_at=value.get("verified_at", ""),
                batch_multiplier=_opt(value.get("batch_multiplier")),
                region_multiplier=_opt(value.get("region_multiplier")),
                fast_multiplier=_opt(value.get("fast_multiplier")),
                long_context_flag_tokens=(
                    int(value["long_context_flag_tokens"])
                    if value.get("long_context_flag_tokens") is not None
                    else None
                ),
                note=value.get("note", ""),
            )
            for key, value in (raw.get("sources") or {}).items()
        }
        version = str(raw.get("version", "desconocida"))
        logger.info(
            "tabla de precios %s cargada: %d modelos verificados y %d de LiteLLM",
            version,
            len(models),
            len(unverified),
        )
        return cls(models, version, sources, unverified, unverified_date)

    @property
    def version(self) -> str:
        return self._version

    @property
    def models(self) -> dict[str, ModelPrice]:
        """Los modelos de la capa verificada (y las tarifas propias)."""
        return dict(self._models)

    @property
    def sources(self) -> dict[str, Source]:
        return dict(self._sources)

    def lookup(self, model: str | None) -> ModelPrice | None:
        """Coincidencia exacta y, si no, el prefijo más largo que sea seguro.

        Los proveedores versionan los modelos con un snapshot (`gpt-4o-mini-2024-07-18`),
        y ese sí hereda la tarifa del modelo base. Lo que no puede heredarla es otra
        versión del modelo: `claude-opus-4-6` cuesta un tercio que `claude-opus-4`, y
        dejar que el prefijo colara habría cobrado el triple en silencio.
        """
        if not model:
            return None
        clave = model.strip().lower()
        if clave in self._memo:
            return self._memo[clave]
        # Primero el nombre tal cual, en las dos capas y por ese orden: un snapshot que
        # LiteLLM tenga como entrada exacta no le gana al modelo base verificado. Sólo
        # después se le quitan los adornos del gateway. El orden importa: un nombre
        # adornado (`azure/…`, `eu.anthropic.…`, `openrouter/…`) es otro vendedor con su
        # propio precio —Bedrock regional cobra un 10 % más— y, si LiteLLM lo conoce,
        # ese precio sin verificar está más cerca de la factura que el del proveedor.
        candidatos = _candidates(clave)
        literal, pelados = candidatos[:1], candidatos[1:]
        encontrado = (
            _resolve(literal, self._models, self._prefixes)
            or _resolve(literal, self._unverified, self._unverified_prefixes)
            or _resolve(pelados, self._models, self._prefixes)
            or _resolve(pelados, self._unverified, self._unverified_prefixes)
        )
        self._memo[clave] = encontrado
        return encontrado

    # -- cálculo -----------------------------------------------------------------

    def compute(
        self,
        model: str | None,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_write_1h_tokens: int = 0,
        tier: str = "standard",
        region: str = "global",
        today: date | None = None,
    ) -> CostBreakdown:
        """Coste del span cobrando cada tramo de tokens con su metro.

        `input_tokens` es el total facturable de entrada: los tokens leídos de caché y
        los escritos en caché van **dentro** de esa cifra, no aparte (contrato §2). El
        resto —lo que no salió de la caché ni entró en ella— se cobra a tarifa base.
        """
        price = self.lookup(model)
        if price is None:
            if model:
                logger.info("modelo sin tarifa conocida: %s (coste desconocido)", model)
            return CostBreakdown(unknown=True)

        source = self._sources.get(price.source)
        notas: list[str] = []
        sin_verificar = (
            []
            if price.verified
            else [
                "Tarifa no verificada: sale de la tabla comunitaria de LiteLLM "
                f"(descargada el {self._unverified_date or 'día desconocido'}), no de la "
                "página del proveedor. Puede no coincidir con la factura."
            ]
        )

        base_in, base_out = self._tier_rates(price, source, tier, notas)
        multiplicador = self._region_multiplier(price, source, region, notas)
        self._flag_long_context(price, source, input_tokens, notas)
        self._flag_expired(price, today or date.today(), notas)

        # Tarifas de caché. Si el proveedor no publica una, esos tokens van a la tarifa
        # de entrada: es lo que de verdad ocurre, no un descuento inventado.
        lectura = price.cached_input if price.cached_input is not None else base_in
        escritura = price.cache_write if price.cache_write is not None else base_in
        escritura_1h = price.cache_write_1h if price.cache_write_1h is not None else escritura
        # Las tarifas de caché se publican sobre la entrada base; si el metro elegido
        # cambia la entrada (lote, modo rápido), se mueven con ella en la misma
        # proporción, que es como los proveedores dicen que se apilan.
        proporcion = base_in / price.input if price.input else 1.0
        lectura *= proporcion
        escritura *= proporcion
        escritura_1h *= proporcion

        leidos = max(0, cached_input_tokens)
        escritos = max(0, cache_write_tokens)
        escritos_1h = max(0, cache_write_1h_tokens)
        # El total facturable manda: si los tramos suman más que la entrada declarada,
        # se recortan en lugar de cobrar tokens que el proveedor no facturó.
        exceso = leidos + escritos + escritos_1h - max(0, input_tokens)
        if exceso > 0:
            escritos_1h, exceso = max(0, escritos_1h - exceso), max(0, exceso - escritos_1h)
            escritos, exceso = max(0, escritos - exceso), max(0, exceso - escritos)
            leidos = max(0, leidos - exceso)
        normales = max(0, input_tokens - leidos - escritos - escritos_1h)

        cache_read_usd = leidos * lectura / _MILLION * multiplicador
        cache_write_usd = (escritos * escritura + escritos_1h * escritura_1h) / _MILLION
        cache_write_usd *= multiplicador
        input_usd = normales * base_in / _MILLION * multiplicador
        input_usd += cache_read_usd + cache_write_usd
        output_usd = output_tokens * base_out / _MILLION * multiplicador

        # Lo que la caché ya ha ahorrado: esos tokens leídos, a tarifa entera.
        ahorrado = leidos * (base_in - lectura) / _MILLION * multiplicador

        return CostBreakdown(
            input_usd=input_usd,
            output_usd=output_usd,
            total_usd=input_usd + output_usd,
            cache_read_usd=cache_read_usd,
            cache_write_usd=cache_write_usd,
            cache_saving_usd=max(0.0, ahorrado),
            unknown=False,
            assumed=bool(notas),
            unverified=not price.verified,
            rate=(
                f"{price.model} @ {self._version}"
                if price.verified
                else f"{price.model} @ litellm {self._unverified_date}"
            ),
            note=" ".join(sin_verificar + notas),
        )

    # -- metros --------------------------------------------------------------------

    def _tier_rates(
        self, price: ModelPrice, source: Source | None, tier: str, notas: list[str]
    ) -> tuple[float, float]:
        """Tarifas de entrada y salida del metro pedido, o el estándar si no se puede."""
        if tier == "batch":
            factor = source.batch_multiplier if source else None
            if factor is None:
                notas.append(
                    "La llamada dice ir por el API de lotes, pero no tenemos publicado "
                    "el descuento de ese proveedor: se ha cobrado la tarifa estándar."
                )
                return price.input, price.output
            return price.input * factor, price.output * factor

        if tier == "fast":
            if price.fast_input is not None and price.fast_output is not None:
                return price.fast_input, price.fast_output
            factor = source.fast_multiplier if source else None
            if factor is None:
                notas.append(
                    "La llamada pide modo rápido y el proveedor no publica tarifa de "
                    "modo rápido para este modelo: se ha cobrado la estándar."
                )
                return price.input, price.output
            return price.input * factor, price.output * factor

        if tier not in ("", "standard"):
            # `flex`, `scale`… existen y no tenemos su precio. Cobrarlos como estándar
            # sin decirlo sería inventarse la factura de alguien.
            notas.append(
                f"La llamada pide el nivel de servicio «{tier}», del que no tenemos "
                f"tarifa publicada: se ha cobrado la estándar."
            )
        return price.input, price.output

    def _region_multiplier(
        self, price: ModelPrice, source: Source | None, region: str, notas: list[str]
    ) -> float:
        if region != "regional":
            return 1.0
        factor = source.region_multiplier if source else None
        if factor is None or not price.region_surcharge:
            notas.append(
                "La llamada va por un extremo con residencia de datos, pero este modelo "
                "no tiene publicado recargo regional: se ha cobrado la tarifa global."
            )
            return 1.0
        return factor

    def _flag_long_context(
        self, price: ModelPrice, source: Source | None, input_tokens: int, notas: list[str]
    ) -> None:
        """El tramo de contexto largo existe, pero nadie publica dónde empieza.

        Cobrarlo por nuestra cuenta sería inventarnos el umbral, y además engordaría el
        ahorro que anunciamos. Se cobra el estándar y se dice que puede quedarse corto.
        """
        if price.long_context_input is None or source is None:
            return
        umbral = source.long_context_flag_tokens
        if umbral is None or input_tokens < umbral:
            return
        notas.append(
            f"Por encima de {umbral:,} tokens de entrada puede aplicar el tramo de "
            f"contexto largo (${price.long_context_input}/1M en lugar de "
            f"${price.input}/1M), pero el proveedor no publica a partir de cuántos "
            f"tokens entra. Se ha cobrado el estándar, que es la lectura conservadora.".replace(
                ",", "."
            )
        )

    def _flag_expired(self, price: ModelPrice, today: date, notas: list[str]) -> None:
        if not price.expires:
            return
        try:
            caduca = date.fromisoformat(price.expires)
        except ValueError:
            return
        if today <= caduca:
            return
        notas.append(
            f"Esta tarifa era promocional y venció el {caduca.isoformat()}. El coste "
            f"que sale de aquí ya no es fiable: hay que reverificar la tabla."
        )

    # -- estado de la tabla ---------------------------------------------------------

    def stale_sources(self, max_age_days: int, today: date | None = None) -> dict[str, int]:
        """Fuentes cuya última verificación pasa de `max_age_days`, con su antigüedad."""
        hoy = today or date.today()
        viejas: dict[str, int] = {}
        for key, source in self._sources.items():
            try:
                antiguedad = (hoy - date.fromisoformat(source.verified_at)).days
            except ValueError:
                viejas[key] = 10**6
                continue
            if antiguedad > max_age_days:
                viejas[key] = antiguedad
        return viejas

    def expired_rates(self, today: date | None = None) -> list[str]:
        """Modelos con tarifa promocional ya vencida."""
        hoy = today or date.today()
        vencidos = []
        for name, price in self._models.items():
            if not price.expires:
                continue
            try:
                if date.fromisoformat(price.expires) < hoy:
                    vencidos.append(name)
            except ValueError:
                vencidos.append(name)
        return sorted(vencidos)


def _opt(value: Any) -> float | None:
    return None if value is None else float(value)


def _resolve(
    candidates: list[str], models: dict[str, ModelPrice], prefixes: list[str]
) -> ModelPrice | None:
    for candidate in candidates:
        direct = models.get(candidate)
        if direct is not None:
            return direct
        for prefix in prefixes:
            if candidate.startswith(prefix) and _SNAPSHOT_SUFFIX.match(candidate[len(prefix) :]):
                return models[prefix]
    return None


def _load_unverified(path: Path | None) -> tuple[dict[str, ModelPrice], str]:
    """La capa de LiteLLM, ya convertida. Si falta o no se lee, no hay capa: todo lo que
    no esté en la verificada vuelve a ser «coste desconocido», nunca cero."""
    if path is None:
        return {}, ""
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.warning("no se pudo leer la capa de LiteLLM en %s; se sigue sin ella", path)
        return {}, ""
    modelos = {
        name: ModelPrice(
            model=name,
            input=float(entry["input"]),
            output=float(entry["output"]),
            cached_input=_opt(entry.get("cached_input")),
            cache_write=_opt(entry.get("cache_write")),
            cache_write_1h=_opt(entry.get("cache_write_1h")),
            fast_input=_opt(entry.get("fast_input")),
            fast_output=_opt(entry.get("fast_output")),
            source="litellm",
            verified=False,
        )
        for name, entry in (raw.get("models") or {}).items()
    }
    return modelos, str(raw.get("fetched_at", ""))


#: Los modos de LiteLLM que se cobran por token de texto, que es lo que mide el contrato.
_MODOS_DE_TEXTO = ("chat", "responses", "completion")

#: Metro de LiteLLM (USD por token) → metro nuestro (USD por millón).
_METROS_LITELLM = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "cached_input": "cache_read_input_token_cost",
    "cache_write": "cache_creation_input_token_cost",
    "cache_write_1h": "cache_creation_input_token_cost_above_1hr",
    "fast_input": "input_cost_per_token_priority",
    "fast_output": "output_cost_per_token_priority",
}


def convertir_litellm(raw: dict[str, Any]) -> dict[str, dict[str, float]]:
    """La tabla de LiteLLM, en nuestro formato y sólo con lo que se puede sostener.

    * Sólo modelos de texto: una imagen o un embedding se cobran por otras unidades.
    * Sólo con entrada y salida numéricas. Un modelo a cero en las dos no entra: para
      Laplace «cuesta cero» es una afirmación, y ésa no la ha verificado nadie.
    * Nombres en minúsculas, que es como compara la búsqueda.
    * Los tramos de contexto largo, lote y regiones de LiteLLM no se cargan: la capa
      entera ya va marcada como no verificada, y esos metros los aplica la verificada.
    """
    salida: dict[str, dict[str, float]] = {}
    for nombre, entrada in raw.items():
        if nombre == "sample_spec" or not isinstance(entrada, dict):
            continue
        if entrada.get("mode") not in _MODOS_DE_TEXTO:
            continue
        precio: dict[str, float] = {}
        for nuestro, suyo in _METROS_LITELLM.items():
            valor = entrada.get(suyo)
            if valor is None:
                continue
            if isinstance(valor, bool) or not isinstance(valor, (int, float)):
                precio = {}
                break
            precio[nuestro] = round(float(valor) * _MILLION, 6)
        if "input" not in precio or "output" not in precio:
            continue
        if precio["input"] <= 0 and precio["output"] <= 0:
            continue
        salida[str(nombre).strip().lower()] = precio
    return dict(sorted(salida.items()))


_table: PriceTable | None = None
#: Tarifas propias guardadas desde la interfaz: modelo → entrada con el formato de la
#: tabla. Viven en la base de metadatos; aquí sólo la copia en memoria.
_custom: dict[str, dict[str, Any]] = {}


def get_price_table() -> PriceTable:
    global _table
    if _table is None:
        _table = PriceTable.load()
    return _table


def reload_price_table() -> PriceTable:
    """Recarga la tabla sin reiniciar el proceso."""
    global _table
    _table = PriceTable.load()
    return _table


def set_custom_prices(models: dict[str, dict[str, Any]]) -> PriceTable:
    """Sustituye las tarifas propias y recarga la tabla."""
    global _custom
    _custom = {
        nombre: {**entrada, "source": entrada.get("source") or "propia"}
        for nombre, entrada in models.items()
    }
    return reload_price_table()


def custom_prices() -> dict[str, dict[str, Any]]:
    return dict(_custom)
