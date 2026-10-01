"""El gráfico del Diagnóstico: cuándo se gastó, cuánto sobraba y en qué pasos (D-152).

Dos vistas de la misma ventana que el héroe:

* **El gasto por tramo** —un día, o una hora si la ventana es corta— con la franja
  evitable encima. El gasto está medido. La franja no lo está tramo a tramo: las reglas
  miden lo evitable de cada hallazgo en la ventana entera. Se reparte por tramos **en
  proporción a lo que gastó ese día el paso del hallazgo**, así que la suma de los
  tramos es exactamente lo evitable del héroe —nada se cuenta dos veces ni se inventa— y
  un día sin actividad de ese paso no recibe nada. Lo que es un reparto se dice en la
  pantalla, junto al gráfico.
* **El coste por paso**, los que más gastan, con su parte evitable.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from ..storage.base import StepCostSeries, Window, disambiguate
from .modelos import Finding, GastoPaso, Grafico, TramoGasto, reparto

#: Pasos que se enseñan con nombre; el resto va sumado en una fila.
MAX_PASOS = 6


def ancho_de_tramo(window: Window) -> int:
    """Minutos por tramo: días a partir de tres días de ventana, horas por debajo."""
    return 1440 if window.until - window.since >= timedelta(days=3) else 60


def construir(
    store: Any, project_id: str, window: Window, findings: list[Finding]
) -> Grafico | None:
    ancho = ancho_de_tramo(window)
    serie = store.step_cost_series(project_id, window, ancho)
    if not isinstance(serie, StepCostSeries) or not any(serie.total):
        return None

    n = len(serie.total)
    evitable = [0.0] * n
    sin_repartir = 0.0
    por_paso: dict[str, float] = {}
    for f in findings:
        if f.window_waste_usd <= 0:
            continue
        # Un hallazgo de varios pasos se reparte entre ellos (D-178).
        for paso, dinero in (reparto(f) or {"": f.window_waste_usd}).items():
            por_paso[paso] = por_paso.get(paso, 0.0) + dinero
            pesos = serie.pasos.get(paso) or []
            suma = sum(pesos)
            if suma <= 0:
                # El paso no tiene gasto en la serie: no hay con qué repartirlo, y se
                # dice cuánto es en vez de colocarlo en un día cualquiera.
                sin_repartir += dinero
                continue
            for i, coste in enumerate(pesos):
                evitable[i] += dinero * coste / suma

    tramos = [
        TramoGasto(
            start=window.since + timedelta(minutes=ancho * i),
            cost_usd=serie.total[i],
            # Un tramo nunca puede tener más evitable que gasto: si dos hallazgos del
            # mismo paso lo rozan, manda lo medido.
            avoidable_usd=min(evitable[i], serie.total[i]),
        )
        for i in range(n)
    ]

    costes = sorted(
        ((sum(v), k) for k, v in serie.pasos.items()), key=lambda par: (-par[0], par[1])
    )
    # Nombrados como en el resto del producto: dos pasos homónimos —la misma función
    # llamada desde sitios distintos, o con instrucciones distintas— no pueden salir
    # como tres filas «responder» con cifras distintas.
    filas = []
    for coste, clave in costes[:MAX_PASOS]:
        etiqueta, sitio, pista = serie.nombres.get(clave, (clave, "", ""))
        filas.append(SimpleNamespace(key=clave, name=etiqueta or clave, site=sitio,
                                     hint=pista, coste=coste))
    pasos = [
        GastoPaso(
            key=f.key,
            name=f.name,
            cost_usd=f.coste,
            avoidable_usd=min(por_paso.get(f.key, 0.0), f.coste),
        )
        for f in disambiguate(filas)
    ]
    return Grafico(
        bucket_minutes=ancho,
        buckets=tramos,
        steps=pasos,
        other_steps_usd=sum(c for c, _ in costes[MAX_PASOS:]),
        unattributed_usd=sin_repartir,
    )
