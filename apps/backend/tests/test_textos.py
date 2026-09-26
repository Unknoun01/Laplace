"""Los catálogos del backend: completos, con los mismos huecos y en espejo con la web (D-148).

Un hueco perdido en una traducción no falla al compilar: falla al redactar, con un
`KeyError` en mitad del Diagnóstico de quien lo lee en chino, o deja la cifra fuera de la
frase. Y una duración que el motor escribe distinta de la web se lee al lado de la otra.
"""

from __future__ import annotations

import re

import pytest
from nodo import ejecutar

from laplace_backend import idioma, textos
from laplace_backend.insights.modelos import span_label, window_label

ORIGEN = textos.CATALOGOS["es"]


def _pareja(clave: str) -> list[str] | None:
    base = re.sub(r"_(one|other)$", "", clave)
    if base == clave:
        return None
    return [f"{base}_one", f"{base}_other"]


@pytest.mark.parametrize("lengua", idioma.IDIOMAS)
def test_mismas_claves_y_huecos_que_el_espanol(lengua):
    catalogo = textos.CATALOGOS[lengua]
    assert set(catalogo) == set(ORIGEN), set(catalogo) ^ set(ORIGEN)
    malos = []
    for clave, texto in ORIGEN.items():
        tiene = textos.huecos(catalogo[clave])
        pareja = _pareja(clave)
        if pareja:
            # En un plural la forma singular puede llevar la cifra o no («过去 1 小时»,
            # «el último día»); los demás huecos son obligatorios.
            todos = set().union(*(textos.huecos(ORIGEN[c]) for c in pareja))
            bien = tiene <= todos and todos - {"n"} <= tiene
        else:
            bien = tiene == textos.huecos(texto)
        if not bien:
            malos.append((clave, catalogo[clave]))
    assert malos == []


def test_ningun_texto_vacio_y_plurales_por_parejas():
    for lengua, catalogo in textos.CATALOGOS.items():
        assert all(v.strip() for v in catalogo.values()), lengua
    for clave in ORIGEN:
        pareja = _pareja(clave)
        if pareja:
            assert all(c in ORIGEN for c in pareja), clave


def test_una_clave_que_no_existe_falla_alto():
    """Devolver la clave o el español escondería el fallo justo en el idioma que nadie
    del equipo lee."""
    with pytest.raises(KeyError):
        textos.t("no.existe")


def test_las_reglas_de_plural_son_las_de_intl():
    numeros = [0, 1, 1.0, 1.5, 2, 2.5, 5, 21, 100, 0.5]
    web = ejecutar(
        f"""
const res = {{}};
for (const [l, e] of Object.entries({{es: "es-ES", en: "en-US", pt: "pt-BR", fr: "fr-FR", zh: "zh-CN"}})) {{
  const r = new Intl.PluralRules(e);
  res[l] = {numeros}.map((n) => {{ const f = r.select(n); return f === "one" ? "one" : "other"; }});
}}
salida(res);
"""
    )
    for lengua in idioma.IDIOMAS:
        assert [textos.forma_plural(n, lengua) for n in numeros] == web[lengua], lengua


def test_las_duraciones_son_las_mismas_que_en_la_web():
    dias = [0.0001, 1 / 24 / 60, 0.5 / 24, 1 / 24, 2 / 24, 0.4, 1, 1.02, 2.5, 30]
    web = ejecutar(
        f"""
const f = await web("lib/format.ts");
const i = await web("lib/idioma.ts");
const res = {{}};
for (const l of i.IDIOMAS) {{
  i.fijarIdioma(l);
  res[l] = {dias}.map((d) => [f.spanLabel(d), f.windowLabel(d)]);
}}
salida(res);
"""
    )
    for lengua in idioma.IDIOMAS:
        with idioma.usar(lengua):
            aqui = [[span_label(d), window_label(d)] for d in dias]
        assert aqui == web[lengua], lengua
