"""Los temas de la interfaz (D-155, D-158, D-159): el oscuro por defecto, el claro Rose
Gold con cristal líquido, la variante de alto contraste de cada uno, y el contraste de
cada tinta de texto medido en los cuatro.

La auditoría del rediseño (docs/auditoria-rediseno.md) midió el contraste a mano y
encontró dos colores por debajo de AA en el tema claro; los dos se arreglaron y nada
impedía que el siguiente cambio de paleta los rompiera otra vez. Aquí se leen los
tokens de `globals.css`, se resuelven sus `var()` y `color-mix()` como lo hace el
navegador (en sRGB), se compone el cristal translúcido sobre cada punto del degradado
de fondo y se exige 4,5:1 a todo lo que es letra (7:1 con alto contraste).

Aproximación declarada, la misma de la auditoría: ignora el desenfoque y los
resplandores de detrás del cristal.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parents[3] / "apps" / "web" / "app" / "globals.css"
LAYOUT = CSS.parent / "layout.tsx"

#: Las tintas que se usan como letra. `--ink-4` no está: es para separadores y lo
#: decorativo (D-132), y se le pide 3:1 aparte.
TINTAS_DE_TEXTO = ("text", "ink-2", "ink-3", "iris", "teal", "amber", "rose", "rose-ink")
AA = 4.5
AAA = 7.0
DECORATIVO = 3.0
TEMAS = ("oscuro", "claro", "oscuro-ac", "claro-ac")


def _bloque(css: str, selector: str) -> str:
    """El cuerpo del primer bloque con ese selector exacto."""
    inicio = css.index(selector + " {")
    abre = css.index("{", inicio)
    profundidad = 0
    for i in range(abre, len(css)):
        if css[i] == "{":
            profundidad += 1
        elif css[i] == "}":
            profundidad -= 1
            if profundidad == 0:
                return css[abre + 1 : i]
    raise AssertionError(f"bloque sin cerrar: {selector}")


def _variables(cuerpo: str) -> dict[str, str]:
    sin_comentarios = re.sub(r"/\*.*?\*/", "", cuerpo, flags=re.S)
    return {
        m.group(1): " ".join(m.group(2).split())
        for m in re.finditer(r"--([\w-]+)\s*:\s*([^;]+);", sin_comentarios)
    }


def _tema(nombre: str) -> dict[str, str]:
    css = CSS.read_text(encoding="utf-8")
    # El primer `:root` es el tema por defecto; el segundo, los alias comunes (que
    # derivan de las variables base y valen para los dos).
    raiz = _variables(_bloque(css, ":root"))
    alias_inicio = css.index("/* Los tokens de siempre")
    alias = _variables(_bloque(css[alias_inicio:], ":root"))
    claro = _variables(_bloque(css, ':root[data-theme="light"]'))
    alto = _variables(_bloque(css, ':root[data-contrast="high"]'))
    claro_alto = _variables(_bloque(css, ':root[data-theme="light"][data-contrast="high"]'))
    # El mismo orden de capas que la cascada: lo más específico, lo último.
    return {
        "oscuro": {**alias, **raiz},
        "claro": {**alias, **raiz, **claro},
        "oscuro-ac": {**alias, **raiz, **alto},
        "claro-ac": {**alias, **raiz, **alto, **claro, **claro_alto},
    }[nombre]


# ---------------------------------------------------------------------------------
# Colores: lo justo para resolver lo que hay en la hoja
# ---------------------------------------------------------------------------------

Color = tuple[float, float, float, float]  # r, g, b en 0..255; alfa en 0..1


def _partir(argumentos: str) -> list[str]:
    partes, profundidad, actual = [], 0, ""
    for c in argumentos:
        if c == "," and profundidad == 0:
            partes.append(actual.strip())
            actual = ""
            continue
        profundidad += c == "("
        profundidad -= c == ")"
        actual += c
    partes.append(actual.strip())
    return partes


def _color(valor: str, tema: dict[str, str], vistos: tuple[str, ...] = ()) -> Color:
    valor = valor.strip()
    if valor.startswith("var("):
        nombre = valor[4:-1].strip().removeprefix("--")
        assert nombre not in vistos, f"variable circular: {nombre}"
        return _color(tema[nombre], tema, (*vistos, nombre))
    if valor == "transparent":
        return (0.0, 0.0, 0.0, 0.0)
    if valor.startswith("#"):
        h = valor[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    if valor.startswith("rgba(") or valor.startswith("rgb("):
        numeros = [float(x) for x in re.findall(r"[\d.]+", valor)]
        return (numeros[0], numeros[1], numeros[2], numeros[3] if len(numeros) > 3 else 1.0)
    if valor.startswith("color-mix("):
        espacio, a, b = _partir(valor[len("color-mix(") : -1])
        assert espacio == "in srgb", valor
        return _mezcla(a, b, tema, vistos)
    raise AssertionError(f"color que la prueba no sabe leer: {valor}")


def _con_porcentaje(parte: str) -> tuple[str, float | None]:
    m = re.match(r"^(.*?)\s+([\d.]+)%$", parte)
    return (m.group(1), float(m.group(2)) / 100) if m else (parte, None)


def _mezcla(a: str, b: str, tema: dict[str, str], vistos: tuple[str, ...]) -> Color:
    """`color-mix` en sRGB, con alfa premultiplicado, como manda CSS Color 5."""
    (ca, pa), (cb, pb) = _con_porcentaje(a), _con_porcentaje(b)
    if pa is None and pb is None:
        pa, pb = 0.5, 0.5
    elif pa is None:
        pa = 1 - pb
    elif pb is None:
        pb = 1 - pa
    x, y = _color(ca, tema, vistos), _color(cb, tema, vistos)
    alfa = x[3] * pa + y[3] * pb
    if alfa == 0:
        return (0.0, 0.0, 0.0, 0.0)
    canales = [(x[i] * x[3] * pa + y[i] * y[3] * pb) / alfa for i in range(3)]
    return (canales[0], canales[1], canales[2], alfa)


def _sobre(arriba: Color, abajo: Color) -> Color:
    a = arriba[3]
    return (*(arriba[i] * a + abajo[i] * (1 - a) for i in range(3)), 1.0)


def _luminancia(c: Color) -> float:
    def lineal(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (lineal(v) for v in c[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contraste(x: Color, y: Color) -> float:
    lx, ly = _luminancia(x), _luminancia(y)
    return (max(lx, ly) + 0.05) / (min(lx, ly) + 0.05)


def _fondos(tema: dict[str, str]) -> dict[str, Color]:
    """El cristal sobre cada punto del degradado, y el cristal fuerte de los menús."""
    salida = {}
    for punto in ("bg-gradient-start", "bg-gradient-mid", "bg-gradient-end"):
        fondo = _color(tema[punto], tema)
        salida[f"cristal sobre {punto}"] = _sobre(_color(tema["glass"], tema), fondo)
    medio = _color(tema["bg-gradient-mid"], tema)
    salida["cristal fuerte"] = _sobre(_color(tema["glass-strong"], tema), medio)
    return salida


# ---------------------------------------------------------------------------------
# Pruebas
# ---------------------------------------------------------------------------------


def test_el_tema_por_defecto_es_el_oscuro():
    """La insignia es el oscuro: quien no ha elegido nada lo ve, tenga el sistema como
    lo tenga. El claro sólo entra por `data-theme`."""
    css = CSS.read_text(encoding="utf-8")
    raiz = _bloque(css, ":root")
    assert "color-scheme: dark" in raiz
    assert _luminancia(_color(_tema("oscuro")["bg-gradient-mid"], _tema("oscuro"))) < 0.05
    # Ningún `prefers-color-scheme: dark` que cambie nada: ya es lo de partida.
    assert "prefers-color-scheme: dark" not in css
    # Y el claro del sistema sólo se aplica a quien pidió seguir al sistema, con y sin
    # alto contraste: las dos únicas consultas al sistema.
    assert re.search(
        r'@media \(prefers-color-scheme: light\) \{\s*:root\[data-theme="system"\] \{', css
    )
    assert re.search(
        r'@media \(prefers-color-scheme: light\) \{\s*'
        r':root\[data-theme="system"\]\[data-contrast="high"\] \{',
        css,
    )
    assert css.count("prefers-color-scheme") == 2


def _copia_del_sistema(css: str, selector: str) -> dict[str, str]:
    for m in re.finditer(r"@media \(prefers-color-scheme: light\) \{", css):
        cuerpo = _bloque(css[m.start():], "@media (prefers-color-scheme: light)")
        if cuerpo.strip().startswith(selector + " {"):
            return _variables(_bloque(cuerpo, selector))
    raise AssertionError(f"sin copia del sistema para {selector}")


@pytest.mark.parametrize(
    ("elegido", "sistema"),
    [
        (':root[data-theme="light"]', ':root[data-theme="system"]'),
        (
            ':root[data-theme="light"][data-contrast="high"]',
            ':root[data-theme="system"][data-contrast="high"]',
        ),
    ],
)
def test_las_dos_copias_del_claro_son_iguales(elegido, sistema):
    """Elegido a mano o seguido del sistema, el claro es el mismo, con y sin alto
    contraste: CSS obliga a escribirlo dos veces y esto impide que se separen."""
    css = CSS.read_text(encoding="utf-8")
    assert _variables(_bloque(css, elegido)) == _copia_del_sistema(css, sistema)


def test_el_claro_es_rose_gold_con_cristal():
    """D-158: el claro neutro de D-155 perdía el cristal. El fondo es de rubor (rojo por
    encima de verde y de azul), el acento principal es rose gold, el cristal deja ver
    lo de detrás y lleva su filo de luz, y detrás hay manchas de color."""
    tema = _tema("claro")
    for punto in ("bg-gradient-start", "bg-gradient-mid", "bg-gradient-end"):
        r, g, b, _ = _color(tema[punto], tema)
        assert r > g >= b - 2 and r - b >= 10, (punto, tema[punto])
    assert 0 <= _tono(_color(tema["accent-1"], tema)) <= 20
    assert _color(tema["glass"], tema)[3] <= 0.5, "sin transparencia no hay cristal"
    assert "inset" in tema["glass-sheen"]
    assert tema["bg-layers"].count("radial-gradient") >= 2
    assert "saturate" in tema["blur"]
    # El oscuro se queda como estaba: sin filo ni manchas añadidas.
    oscuro = _tema("oscuro")
    assert oscuro["glass-sheen"] == "0 0 #0000" and oscuro["blur"] == "blur(15px)"


def test_el_alto_contraste_no_tiene_transparencias():
    """D-159: sin cristal, sin desenfoque y sin resplandores, en los dos temas."""
    for nombre in ("oscuro-ac", "claro-ac"):
        tema = _tema(nombre)
        assert _color(tema["glass"], tema)[3] == 1.0, nombre
        assert tema["blur"] == "none" and tema["glow-color"] == "transparent", nombre
        assert tema["glass-sheen"] == "0 0 #0000", nombre
        # Ni tintes de fondo en las etiquetas y círculos: blanco, negro y el color que
        # significa algo, nada más.
        for tinte in ("teal-bg", "amber-bg", "iris-bg", "rose-bg"):
            assert tema[tinte] == "transparent", (nombre, tinte)
    # Y en el claro no queda el rose gold del tema: la barra de cada tarjeta empezaba
    # en rosa claro y el botón principal era rosa con letra negra.
    claro = _tema("claro-ac")
    assert _luminancia(_color(claro["accent-1"], claro)) < 0.05


def _tono(c: Color) -> float:
    r, g, b = (v / 255 for v in c[:3])
    alto, bajo = max(r, g, b), min(r, g, b)
    if alto == bajo:
        return 0.0
    d = alto - bajo
    if alto == r:
        h = ((g - b) / d) % 6
    elif alto == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60


@pytest.mark.parametrize("nombre", TEMAS)
def test_toda_la_letra_pasa_aa(nombre):
    tema = _tema(nombre)
    minimo = AAA if nombre.endswith("-ac") else AA
    fallan = []
    for fondo_nombre, fondo in _fondos(tema).items():
        for tinta in TINTAS_DE_TEXTO:
            ratio = _contraste(_sobre(_color(f"var(--{tinta})", tema), fondo), fondo)
            if ratio < minimo:
                fallan.append(f"--{tinta} sobre {fondo_nombre}: {ratio:.2f}")
        decorativo = _contraste(_sobre(_color("var(--ink-4)", tema), fondo), fondo)
        if decorativo < (AA if nombre.endswith("-ac") else DECORATIVO):
            fallan.append(f"--ink-4 sobre {fondo_nombre}: {decorativo:.2f}")
    assert fallan == [], f"tema {nombre}: " + "; ".join(fallan)


@pytest.mark.parametrize("nombre", TEMAS)
def test_el_boton_principal_se_lee(nombre):
    """El botón principal pinta `--on-iris` sobre un degradado de acento 1 a acento 2:
    tiene que leerse en los dos extremos."""
    tema = _tema(nombre)
    minimo = AAA if nombre.endswith("-ac") else AA
    letra = _color("var(--on-iris)", tema)
    for acento in ("accent-1", "accent-2"):
        ratio = _contraste(letra, _color(f"var(--{acento})", tema))
        assert ratio >= minimo, f"--on-iris sobre --{acento}: {ratio:.2f}"


def test_el_script_del_layout_respeta_las_tres_elecciones():
    """Sin nada guardado no se toca `data-theme`: sale el oscuro. «Claro», «oscuro» y
    «como el sistema» se guardan y se aplican antes del primer pintado."""
    layout = LAYOUT.read_text(encoding="utf-8")
    assert 't === "light" || t === "dark" || t === "system"' in layout
    # Y el alto contraste, en el mismo script (D-159).
    assert 'localStorage.getItem("laplace.contrast") === "high"' in layout


def test_la_prueba_muerde():
    """La medida no es decorativa: el claro rosa de D-133 con el ámbar de antes de la
    auditoría (58 %) no pasaría."""
    tema = {
        **_tema("claro"),
        "bg-gradient-mid": "#ffe4e1",
        "glass": "rgba(255, 255, 255, 0.45)",
        "text": "#2d2424",
        "accent-3": "#d4af37",
        "amber": "color-mix(in srgb, var(--accent-3) 58%, #3a2400)",
    }
    fondo = _fondos(tema)["cristal sobre bg-gradient-mid"]
    assert _contraste(_sobre(_color("var(--amber)", tema), fondo), fondo) < AA
