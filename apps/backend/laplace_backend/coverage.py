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

from .storage.base import CoverageFacts

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
        señal.unavailable = (
            f"con {total} {'llamada' if total == 1 else 'llamadas'} a modelos, un "
            f"porcentaje no diría nada; hacen falta {MIN_CALLS_FOR_COVERAGE}"
        )
        return señal
    señal.value = counted / total
    señal.level = _level(señal.value)
    señal.consequence = consequence
    señal.fix = fix
    return señal


def _pct(value: float) -> str:
    return f"{value * 100:.0f} %"


def _de_cada_diez(value: float) -> str:
    """«Seis de cada diez» se entiende sin traducir; «el 61 %» hay que traducirlo."""
    faltan = round((1 - value) * 10)
    if faltan <= 1:
        return "alguna llamada suelta"
    return f"{faltan} de cada diez llamadas"


def build(facts: CoverageFacts, *, has_managed_prompts: bool = False) -> Coverage:
    """Las cuatro señales y la lectura, a partir de las cuentas del almacén."""
    total = facts.llm_calls
    cobertura = Coverage(llm_calls=total, split_steps=list(facts.split_steps))

    cobertura.signals.append(
        _signal(
            "pasos",
            "Pasos que se distinguen entre sí",
            facts.identified_steps,
            total,
            consequence=(
                "Las llamadas que no se distinguen caen todas en el mismo montón, así "
                "que las reglas no pueden comparar un paso con otro y se callan. Un "
                "«no hay nada que arreglar» con esta señal baja significa «no lo "
                "sabemos», no «está bien»."
            ),
            fix=(
                "Decora las funciones de tu agente con @laplace.observe(type=\"agent\") "
                "o envuélvelas en laplace.span(...): con eso Laplace sabe desde dónde se "
                "llama al modelo. Si tienes los payloads desactivados "
                "(capture_content=False), tampoco puede usar las instrucciones."
            ),
        )
    )
    cobertura.signals.append(
        _signal(
            "tarifa",
            "Llamadas con tarifa conocida",
            facts.priced,
            total,
            consequence=(
                "Lo que no tiene tarifa no cuesta cero: cuesta «no lo sabemos». Mientras "
                "esta señal no esté al 100 %, el gasto de abajo es un suelo y el ahorro "
                "también."
            ),
            fix=(
                "Si es un modelo de un proveedor, añádelo a "
                "apps/backend/laplace_backend/pricing/model_prices.json con el precio de su "
                "página oficial: es un PR de una línea y la tabla se recarga sin reiniciar. "
                "Si corre en tu máquina —Ollama, LM Studio—, no hay tarifa que añadir: no "
                "te cobra nadie. Entonces esta señal se queda a cero a propósito, el dinero "
                "no se puede calcular y las reglas te hablan de tokens y de tiempo, que sí "
                "están medidos."
            ),
        )
    )
    cobertura.signals.append(
        _signal(
            "tokens",
            "Llamadas con tokens del proveedor",
            facts.measured_tokens,
            total,
            consequence=(
                "Los tokens que contamos nosotros son una aproximación, y el coste que "
                "sale de ellos también. No es lo mismo una cifra que viene de la factura "
                "que una que sale de dividir caracteres entre cuatro. Ojo: una llamada que "
                "falló tampoco trae tokens, y también baja esta señal: ahí no hay nada que "
                "estimar, es que no hubo respuesta."
            ),
            fix=(
                "Mira primero si son llamadas con error —una caída del proveedor las "
                "cuenta todas aquí—. Si no lo son, en streaming pide el recuento: en "
                "OpenAI, stream_options={\"include_usage\": True}."
            ),
        )
    )

    # La versión de prompt es la única de las cuatro que puede estar a cero sin que nada
    # esté mal: significa «no gestionas prompts aquí», que es una decisión legítima. Se
    # enseña igual —es información— pero no cuenta para el veredicto ni pinta de rojo.
    prompts = _signal(
        "prompts",
        "Llamadas con versión de prompt",
        facts.with_prompt_version,
        total,
        consequence=(
            "Sin versión en la traza no se puede decir qué versión de un prompt costó "
            "qué ni cuál acertaba más: la pestaña de Prompts se queda con lo que puede "
            "inferir de las instrucciones."
        ),
        fix=(
            "Saca el prompt a Laplace y pídelo con laplace.get_prompt(\"nombre\"). Es "
            "opcional: el resto del producto funciona igual sin esto."
        ),
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
    return peor.level, peor.level in ("malo", "flojo")


def _reading(cobertura: Coverage) -> tuple[str, str]:
    """La frase de arriba y el porqué. Es lo único que va a leer la mayoría."""
    if cobertura.level == "sin-base":
        motivo = next(
            (s.unavailable for s in cobertura.signals if s.unavailable),
            "todavía no hay llamadas a modelos que mirar",
        )
        return (
            "Todavía no podemos decir cuánto de tu agente entendemos.",
            f"{motivo.capitalize()}. En cuanto haya tráfico, aquí aparecerá qué parte de "
            f"tus llamadas podemos analizar y qué parte se nos escapa.",
        )

    malas = [s for s in cobertura.signals if s.level in ("malo", "flojo")]

    # El paso partido va primero aunque los cuatro porcentajes estén altos: es el caso
    # en el que el producto se calla pareciendo sano, y no hay ningún otro sitio donde
    # se cuente.
    if cobertura.split_steps and not malas:
        nombres = ", ".join(f"«{p}»" for p in cobertura.split_steps[:3])
        cuantos = len(cobertura.split_steps)
        return (
            (
                f"Hay {cuantos} paso cuyas instrucciones cambian en casi cada ejecución: "
                f"{nombres}."
                if cuantos == 1
                else f"Hay {cuantos} pasos cuyas instrucciones cambian en casi cada "
                f"ejecución: {nombres}."
            )
            + " Sobre ellos no podemos decirte nada.",
            "Cuando el prompt de un paso lleva datos variables dentro —una fecha, un "
            "nombre, el contexto del usuario—, cada llamada parece un paso distinto y las "
            "reglas no tienen dos llamadas que comparar: se callan. No es que ese paso "
            "esté bien, es que no lo hemos mirado. Saca esos datos a variables "
            "(con laplace.get_prompt y {{variables}}, o metiéndolos en el mensaje del "
            "usuario en vez de en el de sistema) y volverá a contar como un paso solo.",
        )

    if not malas:
        pasos = next(s for s in cobertura.signals if s.key == "pasos")
        return (
            f"Entendemos el {_pct(pasos.value or 0)} de las llamadas de tu agente.",
            "Las cifras de abajo se calculan sobre eso. Cuando no podemos analizar una "
            "llamada —porque no distinguimos su paso, porque su modelo no está en la "
            "tabla de precios o porque sus tokens son una estimación— lo decimos aquí y "
            "no lo escondemos en el total.",
        )

    peor = malas[0]
    aviso = (
        f"{_de_cada_diez(peor.value or 0).capitalize()} de tu agente se nos escapan."
        if peor.key == "pasos"
        # El texto de la señal ya empieza por «Llamadas…», así que anteponer «de tus
        # llamadas» lo duplicaba: «de tus llamadas llamadas con tarifa conocida».
        else f"Sólo el {_pct(peor.value or 0)} de tus {peor.label.lower()}."
    )
    partidos = ""
    if cobertura.split_steps:
        nombres = ", ".join(f"«{p}»" for p in cobertura.split_steps[:3])
        partidos = (
            f" Pasa sobre todo en {nombres}: sus instrucciones cambian en casi cada "
            f"ejecución, lo que casi siempre significa que llevan datos variables dentro "
            f"—una fecha, un nombre— y eso parte un paso en muchos. Sácalos a variables y "
            f"volverán a contar como uno."
        )
    return (
        f"{aviso} Léelo antes que las cifras de abajo.",
        f"{peor.consequence} {peor.fix}{partidos}",
    )


__all__ = [
    "Coverage",
    "GOOD_ENOUGH",
    "MIN_CALLS_FOR_COVERAGE",
    "POOR",
    "Signal",
    "build",
]
