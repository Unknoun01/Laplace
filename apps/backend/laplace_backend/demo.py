"""La demo entera: un mes de trazas y lo que va con ellas.

`laplace.demo` emite el tráfico por la ingesta normal, fechado en el pasado. Aquí se
escribe lo que no son trazas y hace falta para que cada pantalla enseñe algo:

* el prompt gestionado `atencion`, con la v1 de hace un mes y la v2 de hace nueve días,
  cada una con su fecha real de despliegue;
* anotaciones de una persona del equipo sobre unas noventa ejecuciones, repartidas entre
  las dos versiones, para que Prompts y Evaluaciones tengan acierto con margen;
* un conjunto de casos y la comparación A/B de la respuesta con `gpt-5.6-terra` frente a
  `gpt-5.6-luna`, hecha ayer;
* la repetición marcada como arreglada el día en que se arregló, para que el
  seguimiento enseñe el dinero que ya no se gasta.

Sólo existe en local (`POST /api/demo`): en una instalación compartida, meter datos
inventados es lo último que alguien espera de un botón.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from typing import Any

from laplace import demo as trafico
from laplace.evals import run_dataset
from laplace.schema import Annotation, Dataset, DatasetItem, Prompt

from . import insights, seguimiento
from .storage.base import Window
from .storage.metadata import new_id

logger = logging.getLogger("laplace")

PROYECTO = "demo"
CONJUNTO = "regresiones-atencion"
ANOTADORA = "Marta (soporte)"

#: Acierto «real» de cada versión con el que se anota: v2 no acierta mejor, sólo cuesta
#: más. Es la historia que la pestaña de Prompts tiene que saber contar.
ACIERTO = {1: 0.93, 2: 0.92}
ANOTADAS_POR_VERSION = 45


def cargar_demo(origen: str, store: Any, metadata: Any) -> dict[str, Any]:
    """Borra el proyecto `demo` si existía y lo vuelve a sembrar entero."""
    try:
        metadata.delete_project_data(PROYECTO)
    except Exception:  # noqa: BLE001 - un proyecto que no existía no es un error
        logger.debug("demo: no había metadatos que borrar", exc_info=True)
    store.delete_project(PROYECTO)

    resultado = trafico.generar_mes(origen, PROYECTO)
    rng = random.Random(11)

    _prompts(metadata, resultado)
    _anotaciones(metadata, resultado, rng)
    _comparacion(origen, metadata, resultado, rng)
    arreglado = _arreglo_marcado(store, metadata, resultado)

    return {
        "project_id": PROYECTO,
        "traces": len(resultado.trazas),
        "days": trafico.DIAS,
        "fixed_finding": arreglado,
    }


def _prompts(metadata: Any, resultado: trafico.ResultadoDemo) -> None:
    creado = resultado.momento(trafico.DIAS + 1)
    prompt = metadata.create_prompt(
        Prompt(
            id=new_id("pr"),
            project_id=PROYECTO,
            name=trafico.PROMPT,
            description="Respuesta final al cliente, con el manual de condiciones.",
            created_at=creado,
        )
    )
    metadata.add_prompt_version(
        prompt.id, trafico.PROMPT_V1, notes="primera versión", author="Luis", at=creado
    )
    metadata.set_prompt_production(
        prompt.id, 1, actor="Luis", note="primera versión", at=creado
    )
    v2 = resultado.momento(trafico.DIA_PROMPT_V2)
    metadata.add_prompt_version(
        prompt.id,
        trafico.PROMPT_V2,
        notes="Ejemplos de conversaciones bien resueltas y citar el apartado.",
        author="Ana",
        at=v2 - timedelta(hours=20),
    )
    metadata.set_prompt_production(
        prompt.id, 2, actor="Ana", note="tono más cercano", at=v2
    )


def _anotaciones(
    metadata: Any, resultado: trafico.ResultadoDemo, rng: random.Random
) -> None:
    """Una persona del equipo revisa ejecuciones de las dos versiones."""
    for version, acierto in ACIERTO.items():
        candidatas = [
            t for t in resultado.trazas if t.version_prompt == version and not t.falla
        ]
        for traza in rng.sample(candidatas, min(ANOTADAS_POR_VERSION, len(candidatas))):
            bien = rng.random() < acierto
            metadata.save_annotation(
                PROYECTO,
                Annotation(
                    id=new_id("an"),
                    trace_id=traza.trace_id,
                    source="human",
                    verdict="pass" if bien else "fail",
                    comment=None if bien else "No cita la franquicia de la tarifa básica.",
                    author=ANOTADORA,
                    created_at=traza.cuando + timedelta(hours=rng.uniform(2, 30)),
                ),
            )


def _comparacion(
    origen: str, metadata: Any, resultado: trafico.ResultadoDemo, rng: random.Random
) -> None:
    """El conjunto de casos y las dos tiradas, hechas ayer, anotadas caso a caso."""
    recientes = [t for t in resultado.trazas if not t.falla][-30:]
    creado = resultado.momento(2)
    conjunto = Dataset(
        id=new_id("ds"),
        project_id=PROYECTO,
        name=CONJUNTO,
        description="Treinta ejecuciones recientes de atención al cliente.",
        created_at=creado,
        item_count=len(recientes),
        source_filter={"status": "ok", "limit": 30},
    )
    metadata.create_dataset(
        conjunto,
        [
            DatasetItem(
                id=new_id("it"),
                dataset_id=conjunto.id,
                trace_id=t.trace_id,
                input={"pregunta": t.pregunta},
                expected="Puedes llevar una maleta de mano de hasta 10 kg.",
                created_at=creado,
            )
            for t in recientes
        ],
    )

    # La respuesta con el modelo de siempre y con el barato. Los aciertos quedan tan
    # cerca que los márgenes se solapan: «no se distinguen, y B cuesta un 90 % menos».
    aciertos = {trafico.MODELO_CARO: 28, trafico.MODELO_BARATO: 27}
    with trafico.en_el_pasado(resultado.momento(1)) as reloj:
        for modelo, buenos in aciertos.items():
            reloj.avanzar(1800)

            def agente(entrada: dict[str, Any], modelo: str = modelo) -> str:
                reloj.avanzar(rng.uniform(20, 90))
                return trafico.responder(
                    entrada["pregunta"],
                    trafico.Escenario(
                        version_prompt=2, modelo_respuesta=modelo, repeticion=False
                    ),
                )

            tirada = run_dataset(
                CONJUNTO, agente, variant=modelo, project=PROYECTO, endpoint=origen
            )
            malos = set(rng.sample(tirada.trace_ids, len(tirada.trace_ids) - buenos))
            for trace_id in tirada.trace_ids:
                metadata.save_annotation(
                    PROYECTO,
                    Annotation(
                        id=new_id("an"),
                        trace_id=trace_id,
                        source="human",
                        verdict="fail" if trace_id in malos else "pass",
                        author=ANOTADORA,
                        created_at=resultado.momento(0.8),
                    ),
                )


def _arreglo_marcado(
    store: Any, metadata: Any, resultado: trafico.ResultadoDemo
) -> str | None:
    """La repetición, marcada como arreglada el día en que dejó de ocurrir."""
    ventana = Window(
        since=resultado.momento(trafico.DIAS + 1), until=resultado.ahora, days=trafico.DIAS
    )
    repeticiones = [
        f for f in insights.detect(store, PROYECTO, ventana) if f.kind == "repeticion"
    ]
    if not repeticiones:
        logger.warning("demo: la repetición no ha salido; no se marca ningún arreglo")
        return None
    hallazgo = max(repeticiones, key=lambda f: f.window_waste_usd or 0)
    cuando: datetime = resultado.momento(trafico.DIA_ARREGLO_REPETICION)
    metadata.set_setting(
        PROYECTO,
        seguimiento.clave(hallazgo.id),
        {
            "status": "arreglado",
            "at": cuando.isoformat(),
            "note": "La extracción se hace una vez y se reutiliza.",
        },
    )
    return hallazgo.id
