"""Comprueba lo que ha llegado a `laplace ui` desde los agentes de Node (D-165).

Cada integración va a su proyecto. De cada llamada al modelo se exige lo que hace falta
para que Laplace sirva: modelo, tokens con la caché dentro, coste medido, el prompt de
sistema como primer mensaje (la mitad de la identidad del paso) y la respuesta en texto.
Sale con código 1 si algo falta, diciendo qué.

    python verificar.py [http://localhost:8100]
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8100"
SISTEMA = "Eres un asistente de equipaje."
RESPUESTA = "Una maleta de mano."

#: Proyecto → cuántas llamadas al modelo manda su agente.
ESPERADO = {
    "js-oi-anthropic": 3,
    "js-ol-anthropic": 2,
    "js-vercel": 6,
    "js-langchain": 4,
    "js-langgraph": 2,
    "js-openai-agents": 2,
    "js-mastra": 2,
}

#: Proyectos cuyas llamadas tienen que caer en pasos distintos aunque compartan prompt
#: de sistema: en LangGraph, el nodo es el sitio del paso (D-141).
PASOS_DISTINTOS = {"js-langgraph": 2}

#: LangChain.js suma el token de salida de `message_start` al total de `message_delta`
#: cuando hace streaming con Anthropic: Laplace guarda lo que él dice (D-165).
SALIDA_ACEPTADA = {"js-langchain": {12, 13}}


def _get(ruta: str) -> dict:
    with urllib.request.urlopen(BASE + ruta) as r:
        return json.load(r)


def _spans(nodos: list[dict]):
    for nodo in nodos:
        yield nodo["span"]
        yield from _spans(nodo.get("children") or [])


def _texto(contenido) -> str:
    if isinstance(contenido, list):
        return "".join(str(b.get("text", "")) for b in contenido if isinstance(b, dict))
    return str(contenido)


def main() -> int:
    fallos: list[str] = []
    for proyecto, cuantas in ESPERADO.items():
        trazas = _get(f"/api/traces?project_id={proyecto}&limit=50&since=2020-01-01T00:00:00Z")
        llms = [
            s
            for t in trazas["traces"]
            for s in _spans(_get(f"/api/traces/{t['trace_id']}?project_id={proyecto}")["roots"])
            if s["type"] == "llm"
        ]
        if len(llms) != cuantas:
            fallos.append(f"{proyecto}: {len(llms)} llamadas al modelo, se esperaban {cuantas}")
        for s in llms:
            llm, donde = s["llm"], f"{proyecto} · {s['name']}"
            uso, coste = llm["usage"], llm["cost"]
            if not llm.get("request_model"):
                fallos.append(f"{donde}: sin modelo")
            if (uso["input_tokens"], uso["cached_input_tokens"]) != (1200, 1024):
                fallos.append(f"{donde}: entrada {uso['input_tokens']}/{uso['cached_input_tokens']}")
            if uso["output_tokens"] not in SALIDA_ACEPTADA.get(proyecto, {12}):
                fallos.append(f"{donde}: salida {uso['output_tokens']}")
            if uso["estimated"] or coste["unknown"] or not coste["total_usd"]:
                fallos.append(f"{donde}: coste no medido")
            entrada = llm.get("input_messages") or [{}]
            if entrada[0] != {"role": "system", "content": SISTEMA}:
                fallos.append(f"{donde}: sin prompt de sistema ({entrada[0]})")
            salida = llm.get("output_messages") or [{}]
            if _texto(salida[0].get("content")) != RESPUESTA:
                fallos.append(f"{donde}: respuesta {salida[0]}")
        if proyecto in PASOS_DISTINTOS:
            pasos = {s.get("step_key") for s in llms}
            if len(pasos) != PASOS_DISTINTOS[proyecto]:
                fallos.append(f"{proyecto}: {len(pasos)} pasos, se esperaban {PASOS_DISTINTOS[proyecto]}")
        print(f"{proyecto}: {len(llms)} llamadas")
    for fallo in fallos:
        print("FALLO", fallo)
    print("bien" if not fallos else f"{len(fallos)} fallos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
