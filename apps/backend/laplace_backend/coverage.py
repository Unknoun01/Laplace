"""Cuánto de tu agente entiende Laplace, dicho antes que cualquier cifra de ahorro.

Este módulo existe por el peor fallo que puede tener este producto, que además es
**indistinguible del éxito**: cuando la identidad de un paso se parte —un prompt de
sistema con una fecha dentro genera una huella por llamada—, las reglas se callan por
falta de llamadas. Entonces la pantalla dice «no estás tirando dinero ahora mismo» y eso
se lee como una buena noticia, cuando en realidad significa «no te entendemos».

Lo mismo con las otras tres señales: un modelo sin tarifa hace que el total sea un suelo,
unos tokens estimados hacen que el coste sea una aproximación, y sin versión de prompt no
se puede decir qué versión costó qué. Ninguna de las cuatro rompe nada visiblemente. Las
cuatro hacen que las cifras de abajo signifiquen menos de lo que parecen.

Tres reglas de diseño:

1. **Va delante, no en Avanzado.** Si la cobertura es baja, el usuario tiene que
   enterarse **antes** de leer una cifra de ahorro, no después de buscarla.
2. **Se dice con palabras y con qué hacer.** Un «61 %» no acciona nada. «Seis de cada
   diez llamadas no se distinguen entre sí; decora tus funciones con `@observe`» sí.
3. **La guarda de siempre.** Sin llamadas suficientes no hay porcentaje: `None` y el
   motivo. Un 50 % sacado de dos llamadas es el mismo error que el 468 $/mes sacado de
   una hora de datos (D-073, D-087, y ya van cinco).
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from . import cifras
from .storage.base import CoverageFacts
from .textos import t, tn

logger = logging.getLogger("laplace.coverage")

# ---------------------------------------------------------------------------------
# Umbrales
# ---------------------------------------------------------------------------------

#: Llamadas a modelos por debajo de las cuales no se enseña ningún porcentaje. Con
#: cuatro llamadas, «el 75 % de tus pasos se identifican» no informa de nada.
MIN_CALLS_FOR_COVERAGE = 10

#: Por debajo de esto, una señal está mal y se avisa arriba del todo. No es un número
#: redondo por casualidad: con una de cada diez llamadas sin identificar, las reglas
#: siguen viendo el resto; con una de cada tres, el diagnóstico ya no es de tu agente.
GOOD_ENOUGH = 0.9
POOR = 0.7

Level = Literal["sin-base", "bien", "flojo", "malo", "no-aplica"]


class Signal(BaseModel):
    """Una de las cuatro señales, con su guarda puesta.

    `value` es `None` mientras no haya llamadas suficientes —nunca cero, que se leería
    como «ninguna»— y entonces `unavailable` dice por qué.
    """

    key: Literal["pasos", "tarifa", "tokens", "prompts"]
    label: str
    value: float | None = None
    counted: int = 0
    total: int = 0
    level: Level = "sin-base"
    #: Qué significa que esta señal esté baja, para las cifras de abajo.
    consequence: str = ""
    #: Qué hacer para subirla. Concreto: una línea de código o un ajuste.
    fix: str = ""
    unavailable: str = ""


class Coverage(BaseModel):
    """Lo que Laplace entiende de este proyecto, y lo que no."""

    llm_calls: int = 0
    signals: list[Signal] = Field(default_factory=list)
    #: La peor de las señales que aplican. Manda en el color y en el sitio del bloque.
    level: Level = "sin-base"
    headline: str = ""
    detail: str = ""
    #: True cuando la pantalla debe enseñar esto **antes** que el dinero.
    prominent: bool = False
    #: Pasos cuya identidad se parte en casi tantas versiones como ejecuciones. Es la
    #: forma concreta que toma la fragilidad, y se nombran para poder ir a mirarlos.
    split_steps: list[str] = Field(default_factory=list)
    #: Si el SDK muestrea, qué parte de las cifras de abajo falta y cuánto costaría
    #: (D-181). Vacío sin muestreo. No sube el nivel: muestrear es una decisión, no un
    #: fallo, pero el total que se lee debajo es de lo que llegó y se dice.
    sampling: str = ""


# ---------------------------------------------------------------------------------
# Cálculo
# ---------------------------------------------------------------------------------


def _level(value: float | None) -> Level:
    if value is None:
        return "sin-base"
    if value >= GOOD_ENOUGH:
        return "bien"
    if value >= POOR:
        return "flojo"
    return "malo"


def _signal(
    key: str, label: str, counted: int, total: int, *, consequence: str, fix: str
) -> Signal:
    señal = Signal(key=key, label=label, counted=counted, total=total)
    if total < MIN_CALLS_FOR_COVERAGE:
        señal.unavailable = tn("cobertura.pocas", total, minimo=MIN_CALLS_FOR_COVERAGE)
        return señal
    señal.value = counted / total
    señal.level = _level(señal.value)
    señal.consequence = consequence
    señal.fix = fix
    return señal


def _pct(value: float) -> str:
    return cifras.porcentaje(value)


def _de_cada_diez(value: float) -> str:
    """«Seis de cada diez» se entiende sin traducir; «el 61 %» hay que traducirlo."""
    faltan = round((1 - value) * 10)
    if faltan <= 1:
        return t("cobertura.alguna_suelta")
    return t("cobertura.de_cada_diez", n=faltan)


def build(facts: CoverageFacts, *, has_managed_prompts: bool = False) -> Coverage:
    """Las cuatro señales y la lectura, a partir de las cuentas del almacén."""
    total = facts.llm_calls
    cobertura = Coverage(llm_calls=total, split_steps=list(facts.split_steps))

    for clave, contado in (
        ("pasos", facts.identified_steps),
        ("tarifa", facts.priced),
        ("tokens", facts.measured_tokens),
    ):
        cobertura.signals.append(
            _signal(
                clave,
                t(f"cobertura.{clave}.label"),
                contado,
                total,
                consequence=t(f"cobertura.{clave}.consecuencia"),
                fix=t(f"cobertura.{clave}.arreglo"),
            )
        )

    # La versión de prompt es la única de las cuatro que puede estar a cero sin que nada
    # esté mal: significa «no gestionas prompts aquí», que es una decisión legítima. Se
    # enseña igual —es información— pero no cuenta para el veredicto ni pinta de rojo.
    prompts = _signal(
        "prompts",
        t("cobertura.prompts.label"),
        facts.with_prompt_version,
        total,
        consequence=t("cobertura.prompts.consecuencia"),
        fix=t("cobertura.prompts.arreglo"),
    )
    # No depende de que el contador esté a cero, sino de si el proyecto gestiona
    # prompts. Un proyecto que borró su prompt conserva trazas que lo mencionan, y
    # enseñarle un «40 %» en rojo por eso sería reprocharle algo que ya no existe.
    if not has_managed_prompts:
        prompts.level = "no-aplica"
        prompts.consequence = ""
    cobertura.signals.append(prompts)

    cobertura.level, cobertura.prominent = _verdict(cobertura.signals)

    # Un paso partido no baja ninguno de los cuatro porcentajes: cada llamada suya tiene
    # identidad, lo que pasa es que tiene una **distinta**, así que el paso no se agrupa
    # y las reglas no lo ven. Si no se subiera el nivel aquí, la pantalla diría
    # «entendemos el 100 % de tus llamadas» mientras el paso más caro del agente es
    # invisible para el motor. Es exactamente el fallo que esta sección existe para
    # destapar, así que manda sobre el resto.
    if cobertura.split_steps and total >= MIN_CALLS_FOR_COVERAGE:
        if cobertura.level in ("bien", "sin-base"):
            cobertura.level = "flojo"
        cobertura.prominent = True

    cobertura.headline, cobertura.detail = _reading(cobertura)
    if facts.sampled_traces:
        cobertura.sampling = tn(
            "cobertura.muestreo",
            facts.sampled_traces,
            representadas=cifras.miles(round(facts.represented_traces)),
            no_visto=cifras.dinero(facts.unseen_cost_usd),
        )
    return cobertura


def _verdict(signals: list[Signal]) -> tuple[Level, bool]:
    """El nivel del bloque entero y si va delante del dinero.

    Manda la peor señal que aplique. Promediar las cuatro escondería justo el caso que
    importa: tres señales perfectas y la de los pasos por los suelos dan una media
    tranquilizadora y un diagnóstico que no vale nada.
    """
    cuentan = [s for s in signals if s.level not in ("no-aplica",)]
    if not cuentan or all(s.level == "sin-base" for s in cuentan):
        return "sin-base", False
    orden = {"malo": 0, "flojo": 1, "sin-base": 2, "bien": 3}
    peor = min((s for s in cuentan), key=lambda s: orden[s.level])
    # Delante del dinero sólo va lo que cambia cómo se lee el dinero. La versión de
    # prompt no toca ninguna cifra del inicio: le sirve a la pestaña de Prompts, y ahí se
    # dice. Con un solo prompt gestionado, abrir el inicio con «léelo antes que las
    # cifras» era alarmar por algo que las cifras no necesitan (D-135).
    delante = any(
        s.level in ("malo", "flojo") for s in cuentan if s.key != "prompts"
    )
    return peor.level, delante


def _reading(cobertura: Coverage) -> tuple[str, str]:
    """La frase de arriba y el porqué. Es lo único que va a leer la mayoría."""
    if cobertura.level == "sin-base":
        motivo = next(
            (s.unavailable for s in cobertura.signals if s.unavailable),
            t("cobertura.sin_llamadas"),
        )
        return (
            t("cobertura.sin_base.titulo"),
            t("cobertura.sin_base.detalle", motivo=motivo[:1].upper() + motivo[1:]),
        )

    malas = [s for s in cobertura.signals if s.level in ("malo", "flojo")]
    nombres = ", ".join(t("comillas", x=p) for p in cobertura.split_steps[:3])

    # El paso partido va primero aunque los cuatro porcentajes estén altos: es el caso
    # en el que el producto se calla pareciendo sano, y no hay ningún otro sitio donde
    # se cuente.
    if cobertura.split_steps and not malas:
        return (
            tn("cobertura.partidos.titulo", len(cobertura.split_steps), nombres=nombres),
            t("cobertura.partidos.detalle"),
        )

    if not malas:
        pasos = next(s for s in cobertura.signals if s.key == "pasos")
        return (
            t("cobertura.bien.titulo", parte=_pct(pasos.value or 0)),
            t("cobertura.bien.detalle"),
        )

    peor = malas[0]
    if peor.key == "pasos":
        de_cada = _de_cada_diez(peor.value or 0)
        aviso = t("cobertura.aviso.pasos", parte=de_cada[:1].upper() + de_cada[1:])
    else:
        # El texto de la señal ya empieza por «Llamadas…», así que anteponer «de tus
        # llamadas» lo duplicaba: «de tus llamadas llamadas con tarifa conocida».
        aviso = t("cobertura.aviso.otra", parte=_pct(peor.value or 0), senal=peor.label.lower())
    partidos = t("cobertura.partidos.cola", nombres=nombres) if cobertura.split_steps else ""
    return (
        t("cobertura.leelo_antes", aviso=aviso) if cobertura.prominent else aviso,
        # El «qué hacer» ya sale debajo, en la señal que va mal: repetirlo aquí era
        # leer el mismo párrafo dos veces seguidas.
        f"{peor.consequence}{partidos}",
    )


__all__ = [
    "Coverage",
    "GOOD_ENOUGH",
    "MIN_CALLS_FOR_COVERAGE",
    "POOR",
    "Signal",
    "build",
]
