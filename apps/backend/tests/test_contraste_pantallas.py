"""El contraste medido sobre lo que se pinta, pantalla a pantalla (D-176).

`test_tema.py` mide cada tinta contra el cristal compuesto sobre el degradado del fondo,
a partir de los tokens (D-155). Lo que no entra en esa cuenta es lo que sólo existe en
pantalla: los resplandores de detrás del cristal, los degradados de los botones, una
tarjeta con su fondo propio, un texto con opacidad. Aquí se mide lo que se ve:

1. se apunta cada trozo de texto visible con su color, ya compuesto con la opacidad de
   sus antepasados, y su tamaño;
2. se vuelve transparente todo el texto y se hace una captura: lo que queda debajo de
   cada trozo es su fondo de verdad;
3. para cada trozo se calcula el contraste contra cada píxel de su fondo y se exige al
   percentil 10 lo que pide WCAG AA: 4,5:1, o 3:1 si la letra es grande (24 px, o
   18,66 px en negrita). El percentil y no el mínimo, porque en la caja de un texto
   caen también bordes y bordes de iconos que no son su fondo.

En los cuatro temas, en las siete pantallas con la demo cargada, a 1440 px. El texto de
un botón deshabilitado, de un `placeholder` o de un SVG (los gráficos tienen su prueba)
no cuenta: WCAG exime los dos primeros.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_pantallas import PANTALLAS, navegador, servidor  # noqa: E402, F401

pytestmark = pytest.mark.pantallas

TEMAS = {
    "oscuro": {"laplace.theme": "dark"},
    "claro": {"laplace.theme": "light"},
    "oscuro-ac": {"laplace.theme": "dark", "laplace.contrast": "high"},
    "claro-ac": {"laplace.theme": "light", "laplace.contrast": "high"},
}

#: Apunta los trozos de texto: rectángulos en coordenadas del documento, color en sRGB
#: (se normaliza pintándolo en un canvas, que entiende `color-mix` y `oklab`) y tamaño.
APUNTAR = r"""
() => {
  const lienzo = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
  const aRgba = (css) => {
    lienzo.clearRect(0, 0, 1, 1);
    lienzo.fillStyle = '#000'; lienzo.fillStyle = css;
    lienzo.fillRect(0, 0, 1, 1);
    const d = lienzo.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  };
  const opacidad = (el) => {
    let o = 1;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement)
      o *= parseFloat(getComputedStyle(e).opacity);
    return o;
  };
  const trozos = [];
  const andar = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = andar.nextNode(); n; n = andar.nextNode()) {
    if (!n.textContent.trim()) continue;
    const el = n.parentElement;
    if (!el || el.closest('svg, script, style, noscript, [hidden], [aria-hidden=true]')) continue;
    if (el.closest('button:disabled, option, select')) continue;
    // Lo que no se pinta no cuenta: dentro de un <details> cerrado sigue habiendo cajas.
    if (!el.checkVisibility({visibilityProperty: true, contentVisibilityAuto: true})) continue;
    const cs = getComputedStyle(el);
    const o = opacidad(el);
    if (o < 0.05) continue;
    const rango = document.createRange();
    rango.selectNodeContents(n);
    const rects = [...rango.getClientRects()]
      .filter(r => r.width >= 2 && r.height >= 6)
      .map(r => [r.left + scrollX, r.top + scrollY, r.width, r.height]);
    if (!rects.length) continue;
    const [r, g, b, a] = aRgba(cs.color);
    const px = parseFloat(cs.fontSize), negrita = parseInt(cs.fontWeight) >= 700;
    trozos.push({
      texto: n.textContent.trim().slice(0, 40),
      donde: el.tagName.toLowerCase() + (el.className && typeof el.className === 'string'
        ? '.' + el.className.trim().split(/\s+/).join('.') : ''),
      rects, color: [r, g, b, a * o],
      grande: px >= 24 || (negrita && px >= 18.66),
    });
  }
  return trozos;
}
"""

#: Esconde el texto sin mover nada: lo que queda es su fondo.
SIN_TEXTO = """
*, *::before, *::after {
  color: transparent !important; -webkit-text-fill-color: transparent !important;
  text-shadow: none !important; caret-color: transparent !important;
  text-decoration-color: transparent !important;
}
"""

#: Carga la captura en un canvas y calcula, trozo a trozo, el percentil 10 del
#: contraste contra los píxeles de su caja.
MEDIR = r"""
async ([png, trozos]) => {
  const img = new Image();
  img.src = 'data:image/png;base64,' + png;
  await img.decode();
  const c = document.createElement('canvas');
  c.width = img.width; c.height = img.height;
  const ctx = c.getContext('2d', {willReadFrequently: true});
  ctx.drawImage(img, 0, 0);
  const lin = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const lum = (r, g, b) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  const salida = [];
  for (const t of trozos) {
    const vals = [];
    for (const [x, y, w, h] of t.rects) {
      const x0 = Math.max(0, Math.floor(x)), y0 = Math.max(0, Math.floor(y));
      const w0 = Math.min(c.width - x0, Math.ceil(w)), h0 = Math.min(c.height - y0, Math.ceil(h));
      if (w0 <= 0 || h0 <= 0) continue;
      const d = ctx.getImageData(x0, y0, w0, h0).data;
      const paso = Math.max(1, Math.floor((w0 * h0) / 400));
      for (let i = 0; i < d.length / 4; i += paso) {
        const [br, bg, bb] = [d[4 * i], d[4 * i + 1], d[4 * i + 2]];
        const [r, g, b, a] = t.color;
        const fr = r * a + br * (1 - a), fg = g * a + bg * (1 - a), fb = b * a + bb * (1 - a);
        const l1 = lum(fr, fg, fb), l2 = lum(br, bg, bb);
        vals.push((Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05));
      }
    }
    if (!vals.length) continue;
    vals.sort((a, b) => a - b);
    const p10 = vals[Math.floor(vals.length * 0.1)];
    const minimo = t.grande ? 3 : 4.5;
    const rgb = t.color.slice(0, 3).join(',');
    if (p10 < minimo)
      salida.push(`${t.donde} «${t.texto}» en rgb(${rgb}): ${p10.toFixed(2)} < ${minimo}`);
  }
  return salida;
}
"""


def _medir(navegador, url: str, espera: str, tema: dict[str, str]) -> list[str]:  # noqa: F811
    contexto = navegador.new_context(viewport={"width": 1440, "height": 900}, locale="es-ES")
    ajustes = "".join(f"localStorage.setItem({k!r}, {v!r});" for k, v in tema.items())
    contexto.add_init_script(f"try {{ {ajustes} }} catch (e) {{}}")
    pagina = contexto.new_page()
    try:
        pagina.goto(url, wait_until="networkidle")
        pagina.wait_for_function(
            "t => document.querySelector('main')?.innerText.includes(t)", arg=espera,
            timeout=15_000,
        )
        # Las animaciones de entrada, quietas: una captura a media transición mide
        # un color que no se queda.
        pagina.add_style_tag(content="*, *::before, *::after { animation: none !important;"
                             " transition: none !important; }")
        # La ventana, tan alta como el documento: una captura de página entera cambia
        # la maqueta por debajo (lo que va en `vh`, lo fijo), y las cajas apuntadas ya
        # no caerían sobre lo capturado.
        alto = 0
        while alto != (nuevo := pagina.evaluate("document.documentElement.scrollHeight")):
            alto = nuevo
            pagina.set_viewport_size({"width": 1440, "height": min(alto, 16_000)})
            pagina.wait_for_timeout(300)
        trozos = pagina.evaluate(APUNTAR)
        estilo = pagina.add_style_tag(content=SIN_TEXTO)
        png = pagina.screenshot(animations="disabled")
        estilo.evaluate("e => e.remove()")
        return pagina.evaluate(MEDIR, [base64.b64encode(png).decode(), trozos])
    finally:
        contexto.close()


@pytest.mark.parametrize("tema", TEMAS)
@pytest.mark.parametrize("pantalla", PANTALLAS)
def test_toda_la_letra_se_lee_sobre_lo_que_tiene_detras(servidor, navegador, pantalla, tema):  # noqa: F811
    ruta, texto = PANTALLAS[pantalla]
    fallos = _medir(navegador, servidor + ruta, texto, TEMAS[tema])
    assert fallos == [], f"{pantalla} en {tema}:\n" + "\n".join(sorted(set(fallos)))
