"""Los catálogos de la web, completos y con los mismos huecos (D-147).

Que falte una clave en un idioma lo caza `tsc`: los catálogos están tipados contra el
español. Lo que `tsc` no ve es un hueco perdido: una traducción al chino sin `{n}` compila
y deja la cifra fuera de la frase, y una con `{proyecto}` mal escrito deja las llaves a la
vista. Eso se comprueba aquí, cargando los catálogos de verdad con Node.
"""

from __future__ import annotations

import re

from nodo import ejecutar

HUECO = re.compile(r"\{(\w+)\}")


def _catalogos() -> dict[str, dict[str, str]]:
    return ejecutar(
        """
const t = await web("lib/textos.ts");
salida(t.CATALOGOS);
"""
    )  # type: ignore[return-value]


def test_cada_traduccion_tiene_los_huecos_del_espanol():
    catalogos = _catalogos()
    origen = catalogos["es"]
    malos = []
    for lengua, catalogo in catalogos.items():
        assert set(catalogo) == set(origen), lengua
        for clave, texto in origen.items():
            tiene = set(HUECO.findall(catalogo[clave]))
            base = re.sub(r"_(one|other)$", "", clave)
            if base != clave:
                # Un plural: la forma singular puede llevar la cifra en un idioma («过去 1
                # 小时») y no en otro («el último día»). Vale cualquier hueco de la
                # pareja, y los que no son `{n}` son obligatorios.
                pareja = HUECO.findall(origen[f"{base}_one"] + origen[f"{base}_other"])
                bien = tiene <= set(pareja) and set(pareja) - {"n"} <= tiene
            else:
                bien = tiene == set(HUECO.findall(texto))
            if not bien:
                malos.append((lengua, clave, catalogo[clave]))
    assert malos == []


def test_ningun_texto_esta_vacio():
    vacios = [
        (lengua, clave)
        for lengua, catalogo in _catalogos().items()
        for clave, texto in catalogo.items()
        if not texto.strip()
    ]
    assert vacios == []


def test_los_plurales_van_por_parejas():
    """`tn()` busca `x_one` y cae a `x_other`: una sin la otra es un plural a medias."""
    claves = set(_catalogos()["es"])
    sueltas = [
        c
        for c in claves
        if (c.endswith("_one") and c[:-4] + "_other" not in claves)
        or (c.endswith("_other") and c[:-6] + "_one" not in claves)
    ]
    assert sueltas == []


def test_la_hoja_de_estilos_no_escribe_texto():
    """El CSS pintaba «¿por qué?» y «avanzado» con `content:` en español, en los cinco
    idiomas (D-152). Un texto así va en una variable que rellena el proveedor de idioma;
    en el CSS sólo puede quedar como valor de reserva dentro de `var()`."""
    from pathlib import Path

    hoja = (Path(__file__).resolve().parents[2] / "web" / "app" / "globals.css").read_text(
        encoding="utf-8"
    )
    escritos = [
        m.group(0)
        for m in re.finditer(r"content:\s*\"[^\"]*[^\W\d_]{2,}[^\"]*\"", hoja)
    ]
    assert escritos == [], escritos
