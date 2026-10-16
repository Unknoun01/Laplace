"""La demo enseña la caché compartida entre pasos (D-209).

D-178 dejó la regla sin ejemplo en la demo: ningún paso compartía instrucciones con otro.
Ahora `verificar_politica` manda el mismo manual que `responder`, desde otro sitio, en las
ejecuciones que hablan de dinero. Estas pruebas fijan que el hallazgo sale, que dice lo que
tiene que decir y que no cobra dos veces lo que ya cobran el contexto fijo y la caché
semántica sobre `responder`.
"""

from __future__ import annotations

from test_catalogo_hallazgos import mes_de_demo  # noqa: F401  (fixture)

from laplace_backend import insights
from laplace_backend.insights.cache_compartida import KIND


def _compartida(store, ventana):
    return [f for f in insights.detect(store, "demo", ventana) if f.kind == KIND]


def test_la_demo_enseña_la_cache_compartida(mes_de_demo):  # noqa: F811
    store, ventana = mes_de_demo
    (f,) = _compartida(store, ventana)
    assert "verificar_politica" in f.summary and "responder" in f.summary, f.summary
    assert f.window_waste_usd > 0 and not f.cost_unavailable
    assert len(f.step_shares) >= 2, "el dinero se reparte entre los pasos que comparten"


def test_no_cobra_otra_vez_lo_que_ya_cobra_el_contexto_fijo(mes_de_demo):  # noqa: F811
    """Sólo cuentan las lecturas del paso nuevo: las de `responder` (su segunda llamada de
    cada ejecución) ya son del contexto fijo y se descuentan (D-117, D-178)."""
    from laplace_backend.insights.cache_compartida import cuentas, grupos

    store, ventana = mes_de_demo
    usos = store.model_usage("demo", ventana, min_calls=1, limit=500)
    (g,) = grupos(usos, store.prefix_traces("demo", ventana))
    c = cuentas(g)
    nuevo = [p for p in g.pasos if p.name.startswith("verificar_politica")]
    assert len(nuevo) == 1 and c is not None
    assert c.ya_por_paso > 0, "responder repite el prefijo y el contexto fijo ya lo cuenta"
    assert c.lecturas == g.tokens * nuevo[0].calls, (c.lecturas, g.tokens, nuevo[0].calls)
