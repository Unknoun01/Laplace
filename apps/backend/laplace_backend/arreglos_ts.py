"""Los arreglos del bot de PR en TypeScript y JavaScript (D-220).

Lo mismo que `arreglos_codigo` hace en Python (D-190), sobre el árbol de `tree-sitter`: se
localiza la llamada anotada, se cambia sólo ese trozo y el resultado tiene que parsear sin
errores. Lo ambiguo (el modelo viene de una variable, el `system` de una función, la
llamada va envuelta) no se toca y se dice por qué. `tree-sitter` y sus gramáticas son el
extra opcional `[typescript]`: sin él, el bot dice que no puede y no escribe a ojo.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .github_bot import EXT_TS, SinPropuesta

EXTENSIONES = EXT_TS  # `.jsx` lo cubre la gramática de JavaScript
CACHE = ' cache_control: { type: "ephemeral" }'
_FUNCIONES = {
    "function_declaration",
    "generator_function_declaration",
    "function_expression",
    "function",
    "arrow_function",
    "method_definition",
}


def _importar() -> tuple[Any, Any, Any]:
    import tree_sitter
    import tree_sitter_javascript
    import tree_sitter_typescript

    return tree_sitter, tree_sitter_javascript, tree_sitter_typescript


def requerir() -> None:
    """Lanza `pr.ts_sin_extra` si el extra no está instalado."""
    try:
        _importar()
    except ImportError as exc:
        raise SinPropuesta("pr.ts_sin_extra") from exc


def _raiz(fuente: str, ruta: str) -> tuple[bytes, Any]:
    try:
        ts, js, tipos = _importar()
    except ImportError as exc:
        raise SinPropuesta("pr.ts_sin_extra") from exc
    if ruta.endswith(".tsx"):
        lenguaje = tipos.language_tsx()
    elif ruta.endswith((".ts", ".mts", ".cts")):
        lenguaje = tipos.language_typescript()
    else:
        lenguaje = js.language()
    datos = fuente.encode("utf-8")
    return datos, ts.Parser(ts.Language(lenguaje)).parse(datos).root_node


def comprobar(fuente: str, ruta: str) -> None:
    """El equivalente a `ast.parse` de Python: el fichero tiene que parsear sin errores."""
    if _raiz(fuente, ruta)[1].has_error:
        raise SinPropuesta("pr.no_compila", ruta=ruta)


def _abrir(fuente: str, ruta: str) -> tuple[bytes, Any]:
    datos, raiz = _raiz(fuente, ruta)
    if raiz.has_error:  # con el original roto, las posiciones no son de fiar
        raise SinPropuesta("pr.no_compila", ruta=ruta)
    return datos, raiz


def _recorrer(nodo: Any) -> Iterator[Any]:
    yield nodo
    for h in nodo.children:
        yield from _recorrer(h)


def _txt(datos: bytes, nodo: Any) -> str:
    return datos[nodo.start_byte : nodo.end_byte].decode("utf-8")


def _clave(datos: bytes, par: Any) -> str | None:
    k = par.child_by_field_name("key")
    if k is None:
        return None
    t = _txt(datos, k)
    return t[1:-1] if k.type == "string" else t


def _pares(objeto: Any) -> list[Any]:
    return [h for h in objeto.named_children if h.type == "pair"]


def _opciones(llamada: Any) -> Any | None:
    """El objeto de opciones: el primer argumento, si es un objeto literal."""
    args = llamada.child_by_field_name("arguments")
    primero = args.named_children[0] if args is not None and args.named_children else None
    return primero if primero is not None and primero.type == "object" else None


def _llamada(datos: bytes, raiz: Any, linea: int) -> tuple[Any, Any]:
    """La llamada al modelo que cubre la línea anotada (la más interior con `model` o
    `messages` en su objeto de opciones) y ese objeto."""
    candidatas = []
    for n in _recorrer(raiz):
        if n.type != "call_expression" or not n.start_point[0] + 1 <= linea <= n.end_point[0] + 1:
            continue
        obj = _opciones(n)
        if obj is not None and any(_clave(datos, p) in ("model", "messages") for p in _pares(obj)):
            candidatas.append((n, obj))
    if not candidatas:
        raise SinPropuesta("pr.sin_llamada", linea=linea)
    return min(candidatas, key=lambda c: (c[0].end_byte - c[0].start_byte, -c[0].start_byte))


def _literal(datos: bytes, nodo: Any) -> str | None:
    """El texto de un literal sin nada dinámico, o `None`."""
    if nodo.type == "string" or (
        nodo.type == "template_string"
        and not any(h.type == "template_substitution" for h in nodo.children)
    ):
        return _txt(datos, nodo)[1:-1]
    return None


def _aplicar(datos: bytes, ediciones: list[tuple[int, int, str]], ruta: str) -> str:
    # De atrás hacia delante: una edición no mueve las posiciones de las otras.
    for desde, hasta, texto in sorted(ediciones, reverse=True):
        datos = datos[:desde] + texto.encode("utf-8") + datos[hasta:]
    nuevo = datos.decode("utf-8")
    comprobar(nuevo, ruta)
    return nuevo


def cambiar_modelo(fuente: str, linea: int, de: str, a: str, ruta: str) -> str:
    """El fichero con `model: "de"` de la llamada de `linea` cambiado por `a`."""
    datos, raiz = _abrir(fuente, ruta)
    _, obj = _llamada(datos, raiz, linea)
    par = next((p for p in _pares(obj) if _clave(datos, p) == "model"), None)
    valor = par.child_by_field_name("value") if par is not None else None
    if valor is None or _literal(datos, valor) != de:
        raise SinPropuesta("pr.modelo_no_literal", modelo=de)
    q = _txt(datos, valor)[0]
    return _aplicar(datos, [(valor.start_byte, valor.end_byte, f"{q}{a}{q}")], ruta)


# --- cache_control ----------------------------------------------------------------


def _declarado(datos: bytes, raiz: Any, llamada: Any, nombre: str) -> Any | None:
    """El valor con que se declara `nombre` (una sola vez) en la función de la llamada o,
    si ahí no, en lo más alto del módulo. Con varias declaraciones no se elige una."""
    ambitos = [
        f
        for f in _recorrer(raiz)
        if f.type in _FUNCIONES and f.start_byte <= llamada.start_byte < f.end_byte
    ]
    ambitos.sort(key=lambda f: f.start_byte, reverse=True)  # la más interior primero
    for ambito in [*ambitos, raiz]:
        if ambito is raiz:
            nodos: list[Any] = []
            for h in raiz.children:
                if h.type == "export_statement":
                    h = h.child_by_field_name("declaration") or h
                nodos.extend(_recorrer(h) if h.type.endswith("declaration") else [])
        else:
            nodos = list(_recorrer(ambito))
        valores = [
            n.child_by_field_name("value")
            for n in nodos
            if n.type == "variable_declarator"
            and (nm := n.child_by_field_name("name")) is not None
            and _txt(datos, nm) == nombre
        ]
        if len(valores) == 1:
            return valores[0]
        if valores:
            return None
    return None


def _es_texto(datos: bytes, raiz: Any, llamada: Any, nodo: Any | None, hondo: int = 0) -> bool:
    if nodo is None or hondo > 4:
        return False
    if nodo.type in ("string", "template_string"):
        return True
    if nodo.type == "parenthesized_expression" and nodo.named_children:
        return _es_texto(datos, raiz, llamada, nodo.named_children[0], hondo + 1)
    if nodo.type == "binary_expression":
        op = nodo.child_by_field_name("operator")
        lados = (nodo.child_by_field_name("left"), nodo.child_by_field_name("right"))
        return (
            op is not None
            and _txt(datos, op) == "+"
            and any(_es_texto(datos, raiz, llamada, x, hondo + 1) for x in lados)
        )
    if nodo.type == "identifier":
        valor = _declarado(datos, raiz, llamada, _txt(datos, nodo))
        return _es_texto(datos, raiz, llamada, valor, hondo + 1)
    return False


def _tiene_cache(datos: bytes, nodo: Any) -> bool:
    return any(n.type == "pair" and _clave(datos, n) == "cache_control" for n in _recorrer(nodo))


def _cache_al_final(lista: Any) -> tuple[int, int, str]:
    """Añade `cache_control` al último bloque de una lista de bloques literal."""
    ultimo = lista.named_children[-1] if lista.named_children else None
    if ultimo is None or ultimo.type != "object" or not _pares(ultimo):
        raise SinPropuesta("pr.system_desconocido")
    if any(h.type not in ("pair", "comment") for h in ultimo.named_children):
        raise SinPropuesta("pr.system_desconocido")  # `...otro` o un método dentro
    fin = _pares(ultimo)[-1].end_byte
    return fin, fin, "," + CACHE


def poner_cache(fuente: str, linea: int, ruta: str) -> str:
    """El fichero con el `system` de la llamada de `linea` marcado con `cache_control`."""
    datos, raiz = _abrir(fuente, ruta)
    llamada, obj = _llamada(datos, raiz, linea)
    if _tiene_cache(datos, llamada):
        raise SinPropuesta("pr.ya_cachea")
    par = next((p for p in _pares(obj) if _clave(datos, p) == "system"), None)
    if par is None:
        raise SinPropuesta("pr.sin_system")
    valor = par.child_by_field_name("value")
    if valor.type == "identifier":
        definido = _declarado(datos, raiz, llamada, _txt(datos, valor))
        if definido is not None and definido.type == "array":
            if _tiene_cache(datos, definido):
                raise SinPropuesta("pr.ya_cachea")
            return _aplicar(datos, [_cache_al_final(definido)], ruta)
    if valor.type == "array":
        return _aplicar(datos, [_cache_al_final(valor)], ruta)
    if not _es_texto(datos, raiz, llamada, valor):
        raise SinPropuesta("pr.system_desconocido")
    bloque = f'[{{ type: "text", text: {_txt(datos, valor)},{CACHE} }}]'
    return _aplicar(datos, [(valor.start_byte, valor.end_byte, bloque)], ruta)


def poner_tope(*_: Any) -> str:
    """El tope de vueltas es `@laplace.guard`, del SDK de Python: el de Node no lo tiene."""
    raise SinPropuesta("pr.tope_no_ts")
