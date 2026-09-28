"""Los envoltorios del árbol de traza (D-160).

Un `@observe` alrededor de una llamada al modelo produce una fila con las mismas
cifras que la de debajo. `STATUS.md` lo dejó abierto: quitarla era esconder el paso del
usuario, y dejarla tal cual hacía leer el dinero dos veces. La fila se queda y sus
cifras se dicen como «=». Esto comprueba la función que decide cuándo.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nodo import ejecutar  # noqa: E402


def _decide(casos: str) -> list[bool]:
    return ejecutar(
        f"""
const {{ esEnvoltorio }} = await web("lib/tree.ts");
const r = (coste, entrada = 0, salida = 0) => ({{
  cost_usd: coste, input_tokens: entrada, output_tokens: salida,
  span_count: 1, error_count: 0,
}});
const nodo = (sub, hijos = [], llm = null) =>
  ({{ span: {{ span_id: "x", name: "n", llm }}, subtree: sub, children: hijos, repeat_count: 1 }});
const hoja = nodo(r(0.01, 100, 20), [], {{ request_model: "m" }});
salida([{casos}].map(esEnvoltorio));
"""
    )


def test_cuando_una_fila_solo_envuelve_a_su_hijo():
    assert _decide(
        """
        nodo(r(0.01, 100, 20), [hoja]),                 // envuelve: mismas cifras
        nodo(r(0.02, 200, 40), [hoja]),                 // añade algo propio
        nodo(r(0.02, 200, 40), [hoja, hoja]),           // dos hijos: es un paso de verdad
        nodo(r(0.01, 100, 20), [hoja], {request_model: "m"}),  // un LLM no envuelve
        hoja,                                           // sin hijos
        nodo(r(0), [nodo(r(0))]),                       // sin nada que repetir
        """
    ) == [True, False, False, False, False, False]
