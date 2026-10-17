"""El estilo calculado de lo que Safari trata distinto: cristal y `color-mix` (D-228).

Safari ha ido por detrás en `backdrop-filter` (con prefijo hasta la 18) y en `color-mix`;
todo el color de la web sale de `color-mix` sobre los tokens y el tema claro depende del
cristal. Esta prueba mira el estilo *calculado*, en los dos temas, con el motor que diga
`LAPLACE_NAVEGADOR` (`webkit` es el de Safari; por defecto, Chromium):

* todo token que se declara con `color-mix` o con un color se resuelve a un color;
* el contenedor de cristal tiene `backdrop-filter` (el claro, con `saturate`) y un fondo
  translúcido, no opaco ni transparente del todo;
* los resplandores de detrás del cristal tienen color.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_pantallas import MOTOR, navegador, servidor  # noqa: E402, F401

pytestmark = pytest.mark.pantallas

TEMAS = {"oscuro": "dark", "claro": "light"}

#: Los tokens que son un color, resueltos como los resuelve el motor: un `var()` que no
#: se entiende deja el color heredado, así que se parte de un centinela.
ESTILO = r"""
() => {
  const raiz = getComputedStyle(document.documentElement);
  const nombres = new Set();
  for (const hoja of document.styleSheets)
    for (const r of hoja.cssRules)
      for (const n of (r.style || [])) if (n.startsWith('--')) nombres.add(n);
  const sonda = document.createElement('i');
  sonda.style.color = 'rgb(1, 2, 3)';
  document.body.appendChild(sonda);
  const sinResolver = [];
  for (const n of nombres) {
    const v = raiz.getPropertyValue(n).trim();
    // Un `color-mix` con un `var()` roto deja el token vacío, no inválido a la vista.
    if (v === '' && !/^--(lightningcss|cjk)/.test(n)) { sinResolver.push(`${n}: vacío`); continue; }
    if (!/^(color-mix\(|#|rgb|hsl|oklch)/.test(v)) continue;
    const hijo = document.createElement('b');
    hijo.style.color = `var(${n})`;
    sonda.appendChild(hijo);
    if (getComputedStyle(hijo).color === 'rgb(1, 2, 3)') sinResolver.push(`${n}: ${v}`);
  }
  const caja = getComputedStyle(document.querySelector('.hero, .card'));
  const brillo = getComputedStyle(document.querySelector('.shell'), '::before');
  const brillo2 = getComputedStyle(document.querySelector('.shell'), '::after');
  return {
    sinResolver,
    filtro: caja.backdropFilter, filtroPrefijo: caja.webkitBackdropFilter,
    fondo: caja.backgroundColor, resplandor: [brillo.backgroundColor, brillo2.backgroundColor],
  };
}
"""


def _alfa(css: str) -> float:
    """Opacidad de un color calculado, sea `rgba(…)` o `color(srgb … / a)`."""
    if "/" in css:
        return float(css.rstrip(")").split("/")[1])
    if css.startswith("rgba"):
        return float(css.rstrip(")").split(",")[3])
    return 1.0


@pytest.mark.parametrize("tema", TEMAS)
def test_el_cristal_y_los_colores_se_resuelven_en_este_motor(servidor, navegador, tema):  # noqa: F811
    contexto = navegador.new_context(locale="es-ES")
    contexto.add_init_script(f"localStorage.setItem('laplace.theme', '{TEMAS[tema]}')")
    pagina = contexto.new_page()
    try:
        pagina.goto(servidor + "/?project=demo&days=7", wait_until="networkidle")
        pagina.locator(".hero, .card").first.wait_for(timeout=15_000)
        e = pagina.evaluate(ESTILO)
        assert e["sinResolver"] == [], f"{MOTOR}: tokens de color que no se resuelven"
        assert e["filtro"] not in ("none", ""), f"{MOTOR}: sin cristal en {tema}"
        if MOTOR == "webkit":  # Chromium no expone el alias con prefijo
            assert e["filtroPrefijo"] == e["filtro"], "sin `-webkit-backdrop-filter`"
        if tema == "claro":
            assert "saturate" in e["filtro"], e["filtro"]
        assert 0 < _alfa(e["fondo"]) < 1, f"el cristal no es translúcido: {e['fondo']}"
        for r in e["resplandor"]:
            assert _alfa(r) > 0, f"resplandor sin color: {r}"
    finally:
        contexto.close()
