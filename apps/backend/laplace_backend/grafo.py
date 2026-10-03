"""El grafo del proyecto: cómo está hecho el agente, sumando todas sus trazas (D-188).

El de la vista de traza (D-153) se arma en la web con el árbol de una ejecución; éste
sale del almacén, para la ventana entera: un nodo por paso, con la misma identidad
(`step_key`, o tipo y nombre), sus llamadas, sus modelos y su coste, y una arista por
cada «este paso llama a este otro», con cuántas veces y lo que costaron esas llamadas.
Es de sólo lectura: lo que se pueda cambiar desde aquí vendrá después.

* **El coste de una arista es el coste propio de las llamadas que lleva**, es decir, lo
  que costaron los spans de destino llamados desde el origen. Sólo cuestan las llamadas
  al modelo, así que una arista hacia una herramienta o un subpaso no lleva dinero: el
  de debajo está en sus propias aristas. Sumar el subárbol entero pediría recorrer cada
  traza, y contaría dos veces el mismo gasto en cuanto se sumasen dos aristas.
* **Un modelo sin tarifa no cuesta cero**: cada nodo y cada arista dicen cuántas
  llamadas no tienen tarifa, y la pantalla dice «sin tarifa» o «≥ X».
* **Las tiradas de evaluación no son tráfico real** y no entran (`RULES_WHERE`).
* Con muchos pasos se quedan los `MAX_NODOS` más llamados, y se dice cuántos y cuántas
  llamadas se han quedado fuera: un grafo de doscientas cajas no se lee.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .storage.base import Window

#: Pasos que se dibujan como mucho.
MAX_NODOS = 40


class GraphNode(BaseModel):
    id: str
    label: str
    type: str
    calls: int
    traces: int
    roots: int
    cost_usd: float
    unknown_cost_spans: int
    #: Llamadas con los tokens contados por el SDK: su coste es una aproximación.
    estimated_spans: int
    errors: int
    models: list[str]


class GraphEdge(BaseModel):
    source: str
    target: str
    calls: int
    cost_usd: float
    unknown_cost_spans: int


class ProjectGraph(BaseModel):
    project_id: str
    days: int
    traces: int
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    #: Pasos y llamadas que no se dibujan por pasar de `MAX_NODOS`.
    hidden_nodes: int = 0
    hidden_calls: int = 0


def del_proyecto(store: Any, project_id: str, window: Window) -> ProjectGraph:
    hechos = store.project_graph(project_id, window)
    # El almacén ya los da por llamadas y por id: el recorte es el mismo en los dos.
    dentro = hechos.steps[:MAX_NODOS]
    fuera = hechos.steps[MAX_NODOS:]
    ids = {p.id for p in dentro}
    return ProjectGraph(
        project_id=project_id,
        days=window.days,
        traces=hechos.traces,
        nodes=[
            GraphNode(
                id=p.id,
                label=p.label,
                type=p.type,
                calls=p.calls,
                traces=p.traces,
                roots=p.roots,
                cost_usd=p.cost_usd,
                unknown_cost_spans=p.unknown_cost_spans,
                estimated_spans=p.estimated_spans,
                errors=p.errors,
                models=list(p.models),
            )
            for p in dentro
        ],
        edges=[
            GraphEdge(
                source=a.source,
                target=a.target,
                calls=a.calls,
                cost_usd=a.cost_usd,
                unknown_cost_spans=a.unknown_cost_spans,
            )
            for a in hechos.links
            if a.source in ids and a.target in ids
        ],
        hidden_nodes=len(fuera),
        hidden_calls=sum(p.calls for p in fuera),
    )
