"""Regla 6 — el mismo prefijo en varios pasos, sin compartir la caché (D-178).

Parte del motor de detección (`laplace_backend.insights`, D-130).

La regla del contexto fijo mira cada paso por separado y supone, por prudencia, que la
caché no sobrevive de una ejecución a la siguiente: un paso que se llama una vez por
ejecución no tiene nada que reutilizar. Pero un agente llama a menudo a varios pasos
**con las mismas instrucciones** —el mismo prompt de sistema, el mismo catálogo de
herramientas— desde sitios distintos de su código. Dentro de una ejecución, ese prefijo
se manda una vez por paso, y la caché del proveedor se lo podría servir a todos menos
al primero. Paso a paso no se ve; por prefijo, sí.

Lo que no puede pasar es reclamar dos veces el mismo dinero (D-117):

* De las lecturas que se cuentan aquí se quitan las que ya cuenta el contexto fijo de
  cada paso, que son las de dentro del propio paso.
* Si a esos pasos se les recomienda un modelo más barato, se tarifa sobre ese modelo,
  como hace el contexto fijo: los arreglos se aplican uno detrás de otro.
* Lo que el proveedor ya está sirviendo de caché no se promete otra vez: el ahorro se
  multiplica por la parte del prefijo que no se cachea.
* Se resta una escritura de caché por ejecución, el sobreprecio de la primera.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import cifras
from ..pricing import get_price_table
from ..storage.base import ModelUsage, WindowSummary
from ..textos import t
from .contexto_fijo import MIN_PARTE_SIN_CACHEAR, _cheaper_model_if_recommended
from .modelos import (
    _MILLION,
    MIN_CALLS_FOR_CONTEXT_RULE,
    MIN_FIXED_INPUT_TOKENS,
    Finding,
    FindingDetail,
    FixStep,
    TechItem,
    _floor_flags,
    _miles,
    _money,
    _projection_sentence,
    _scope_label,
    _to_monthly,
    window_label,
)

KIND = "cache_compartida"


@dataclass
class GrupoPrefijo:
    """Los pasos que mandan el mismo prefijo al mismo modelo."""

    prefix: str
    model: str
    pasos: list[ModelUsage] = field(default_factory=list)
    #: Ejecuciones distintas que llaman a alguno de ellos. No es la suma de las de cada
    #: paso: una ejecución que llama a dos cuenta una vez.
    traces: int = 0

    @property
    def calls(self) -> int:
        return sum(p.calls for p in self.pasos)

    @property
    def tokens(self) -> int:
        """El prefijo, por lo bajo: la llamada más corta de todos los pasos (D-108)."""
        return min(p.min_input_tokens for p in self.pasos)

    @property
    def cached(self) -> int:
        return sum(p.cached_input_tokens for p in self.pasos)

    @property
    def principal(self) -> ModelUsage:
        return max(self.pasos, key=lambda p: (p.calls, p.key))


def grupos(usos: list[ModelUsage], trazas: dict[tuple[str, str], int]) -> list[GrupoPrefijo]:
    """Los prefijos que comparten dos pasos o más, con sus ejecuciones distintas."""
    por_prefijo: dict[tuple[str, str], GrupoPrefijo] = {}
    for uso in usos:
        if not uso.prefix or uso.min_input_tokens <= 0:
            continue
        clave = (uso.prefix, uso.model)
        grupo = por_prefijo.setdefault(clave, GrupoPrefijo(prefix=uso.prefix, model=uso.model))
        grupo.pasos.append(uso)
    salida = []
    for clave, grupo in sorted(por_prefijo.items()):
        if len({p.key for p in grupo.pasos}) < 2:
            continue
        grupo.traces = trazas.get(clave, 0)
        if grupo.traces > 0:
            salida.append(grupo)
    return salida


@dataclass
class Cuentas:
    tokens: int
    lecturas: int
    ya_por_paso: int
    parte: float
    entrada: float
    cacheada: float
    escritura: float
    ahorro: float
    tarifa: str


def cuentas(grupo: GrupoPrefijo) -> Cuentas | None:
    """El ahorro de cachear el prefijo una vez por ejecución, sin lo que ya se reclama."""
    tokens = grupo.tokens
    # Dentro de una ejecución la caché sirve todas las llamadas con ese prefijo menos la
    # primera. De esas, las que repiten paso ya las cuenta el contexto fijo de ese paso.
    lecturas = tokens * max(grupo.calls - grupo.traces, 0)
    ya = tokens * sum(max(p.calls - p.traces, 0) for p in grupo.pasos)
    nuevas = lecturas - ya
    if nuevas <= 0:
        return None
    enviado = tokens * grupo.calls
    parte = max(enviado - grupo.cached, 0) / enviado if enviado else 0.0
    table = get_price_table()
    encadenado = next(
        (m for m in (_cheaper_model_if_recommended(p) for p in grupo.pasos) if m), None
    )
    tarifa = encadenado or grupo.model
    price = table.lookup(tarifa)
    if price is None or price.cached_input is None:
        return Cuentas(tokens, nuevas, ya, parte, 0.0, 0.0, 0.0, 0.0, "")
    ahorro = nuevas * (price.input - price.cached_input) / _MILLION * parte
    escritura = 0.0
    if price.cache_write is not None:
        escritura = tokens * grupo.traces * max(price.cache_write - price.input, 0.0) / _MILLION
    ahorro -= escritura
    return Cuentas(
        tokens, nuevas, ya, parte, price.input, price.cached_input, escritura, ahorro, tarifa
    )


def _reparto(grupo: GrupoPrefijo, dinero: float) -> dict[str, float]:
    """El dinero del hallazgo, repartido entre sus pasos por sus llamadas."""
    total = grupo.calls or 1
    salida: dict[str, float] = {}
    for paso in grupo.pasos:
        salida[paso.key] = salida.get(paso.key, 0.0) + dinero * paso.calls / total
    return salida


def _nombres(grupo: GrupoPrefijo) -> str:
    return ", ".join(sorted({p.name for p in grupo.pasos}))


def hallazgo(
    grupo: GrupoPrefijo, summary: WindowSummary, days: float, base: float | None
) -> Finding | None:
    if grupo.calls < MIN_CALLS_FOR_CONTEXT_RULE or grupo.tokens < MIN_FIXED_INPUT_TOKENS:
        return None
    c = cuentas(grupo)
    if c is None or c.parte < MIN_PARTE_SIN_CACHEAR:
        return None  # nada que no cuente ya otra regla, o el proveedor ya lo cachea
    hay_tarifa = bool(c.tarifa)
    if hay_tarifa and c.ahorro <= 0:
        return None  # la escritura se come lo que se lee: no compensa
    ahorro = max(c.ahorro, 0.0)
    tokens_evitables = int(c.lecturas * c.parte)
    principal = grupo.principal
    precio = (
        t("compartida.precio", ahorro=_money(ahorro))
        if hay_tarifa
        else t("contexto.precio.sin_tarifa")
    )
    return Finding(
        id=f"{KIND}:{grupo.prefix}:{grupo.model}",
        kind=KIND,
        title=t(
            "compartida.titulo", pasos=len({p.key for p in grupo.pasos}), tokens=_miles(c.tokens)
        ),
        summary=t(
            "compartida.resumen",
            pasos=_nombres(grupo),
            tokens=_miles(c.tokens),
            trazas=_miles(grupo.traces),
            llamadas=_miles(grupo.calls),
            precio=precio,
        ),
        lead=t("compartida.lead"),
        window_waste_usd=ahorro,
        window_waste_tokens=tokens_evitables,
        cost_unavailable="" if hay_tarifa else t("hallazgo.fuera_de_tabla", modelo=grupo.model),
        costs_money=ahorro > 0,
        monthly_saving_usd=_to_monthly(ahorro, base) if ahorro > 0 else None,
        observed_days=days,
        **_floor_flags(
            sum(p.unknown_cost_spans for p in grupo.pasos),
            sum(p.assumed_rate_spans for p in grupo.pasos),
            [grupo.model],
        ),
        difficulty="mid",
        difficulty_label=t("dificultad.un_rato"),
        scope_label=_scope_label(grupo.traces, summary.traces),
        tech=[
            TechItem(label=t("tec.pasos"), value=_nombres(grupo)),
            TechItem(label=t("tec.entrada_fija"), value=t("tec.tok", n=c.tokens)),
            TechItem(label=t("tec.llamadas"), value=str(grupo.calls)),
            TechItem(label=t("tec.ejecuciones"), value=str(grupo.traces)),
            TechItem(label=t("tec.servido_cache"), value=t("tec.tok", n=grupo.cached)),
        ],
        sample_trace_id=principal.sample_trace_id,
        step_key=principal.key,
        step_shares=_reparto(grupo, ahorro),
    )


def detalle(finding: Finding, grupo: GrupoPrefijo, consulta: str) -> FindingDetail:
    detalle = FindingDetail(**finding.model_dump())
    c = cuentas(grupo)
    detalle.what_happens = t(
        "compartida.que_pasa",
        pasos=_nombres(grupo),
        tokens=_miles(grupo.tokens),
        trazas=_miles(grupo.traces),
        llamadas=_miles(grupo.calls),
    )
    detalle.why = t("compartida.por_que")
    detalle.detection_explanation = t(
        "compartida.deteccion",
        llamadas=MIN_CALLS_FOR_CONTEXT_RULE,
        minimo=_miles(MIN_FIXED_INPUT_TOKENS),
    )
    detalle.detection_query = consulta.strip()
    detalle.fix_steps = [
        FixStep(
            title=t("compartida.arreglo.igual.titulo"),
            body=t("compartida.arreglo.igual.texto"),
        ),
        FixStep(
            title=t("compartida.arreglo.marca.titulo"),
            body=t("compartida.arreglo.marca.texto"),
            code=t("contexto.arreglo.cache.codigo"),
        ),
    ]
    if c is not None and c.tarifa:
        detalle.savings_calculation = t(
            "compartida.ahorro",
            tokens=_miles(c.tokens),
            llamadas=_miles(grupo.calls),
            trazas=_miles(grupo.traces),
            ya=_miles(c.ya_por_paso),
            lecturas=_miles(c.lecturas),
            parte=cifras.porcentaje(c.parte),
            entrada=cifras.dinero(c.entrada),
            cacheada=cifras.dinero(c.cacheada),
            escritura=cifras.dinero_exacto(c.escritura),
            neto=cifras.dinero_exacto(finding.window_waste_usd),
            ventana=window_label(finding.observed_days),
            proyeccion=_projection_sentence(finding),
        )
        if c.tarifa != grupo.model:
            detalle.savings_calculation += t(
                "contexto.encadenado", barato=c.tarifa, modelo=grupo.model
            )
    detalle.savings_note = t("compartida.nota")
    return detalle


#: La consulta que enseña la ficha: las ejecuciones distintas por prefijo, que es lo que
#: esta regla añade a los usos por paso.
CONSULTA = """
SELECT prefix_hash, request_model, uniqExact(trace_id) AS ejecuciones
FROM spans
WHERE project_id = :proyecto AND start_time BETWEEN :desde AND :hasta
  AND span_type = 'llm' AND prefix_hash != ''
GROUP BY prefix_hash, request_model
"""
