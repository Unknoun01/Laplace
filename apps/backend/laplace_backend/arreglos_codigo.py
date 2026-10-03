"""Los arreglos de más de una línea que propone el bot de PR (D-190).

El cambio de modelo (D-185) es un literal en una línea. `cache_control` y un tope de
vueltas no: hay que envolver una expresión que puede ocupar varias líneas, o añadir un
decorador y un import. Por eso se hacen sobre el árbol de sintaxis de Python y no con
una búsqueda de texto, con la misma idea de siempre:

* **Sólo lo que se puede hacer con seguridad.** Se localiza la llamada (o la función) que
  anotó el SDK, se cambia sólo eso y se respeta el resto del fichero carácter a carácter.
  Lo que no se sabe —un `system` que sale de una función y podría ser ya una lista de
  bloques— no se toca: envolverlo rompería la llamada. Se dice por qué.
* **El fichero resultante compila.** Si no, no hay propuesta.
* **Sólo Python.** Las anotaciones de línea las pone el SDK de Python; para lo demás no
  hay de dónde sacar la llamada.

No hay un modelo de lenguaje escribiendo el cambio: un parche que se puede derivar del
código no necesita a nadie que lo imagine, y uno que no se puede derivar no debería
proponerse sin que alguien lo mire.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .github_bot import SinPropuesta

CACHE_CONTROL = '"cache_control": {"type": "ephemeral"}'
#: Métodos que devuelven texto: si el `system` es una llamada a uno de éstos, es texto.
#: `render` es el de los prompts gestionados de Laplace (`ServedPrompt.render`).
_DEVUELVEN_TEXTO = {"render", "format", "join", "strip", "replace", "lower", "upper"}


# ---------------------------------------------------------------------------------
# Posiciones y ediciones sobre el texto original
# ---------------------------------------------------------------------------------


def _arbol(fuente: str) -> ast.Module:
    try:
        return ast.parse(fuente)
    except SyntaxError as exc:
        raise SinPropuesta("pr.solo_python") from exc


class _Texto:
    """El fichero como texto, con las posiciones del árbol traducidas a índices.

    `ast` da las columnas en bytes UTF-8, no en caracteres: con una tilde antes en la
    misma línea, cortar por la columna tal cual cortaría en mal sitio.
    """

    def __init__(self, fuente: str) -> None:
        self.fuente = fuente
        self.inicios = [0]
        for linea in fuente.splitlines(keepends=True):
            self.inicios.append(self.inicios[-1] + len(linea))
        self.lineas = fuente.splitlines(keepends=True)

    def indice(self, linea: int, col_bytes: int) -> int:
        texto = self.lineas[linea - 1] if linea - 1 < len(self.lineas) else ""
        return self.inicios[linea - 1] + len(texto.encode("utf-8")[:col_bytes].decode("utf-8"))

    def desde(self, nodo: ast.AST) -> int:
        return self.indice(nodo.lineno, nodo.col_offset)  # type: ignore[attr-defined]

    def hasta(self, nodo: ast.AST) -> int:
        return self.indice(nodo.end_lineno, nodo.end_col_offset)  # type: ignore[attr-defined]

    def segmento(self, nodo: ast.AST) -> str:
        return self.fuente[self.desde(nodo) : self.hasta(nodo)]


@dataclass
class _Edicion:
    desde: int
    hasta: int
    texto: str


def _aplicar(fuente: str, ediciones: list[_Edicion]) -> str:
    # De atrás hacia delante: así una edición no mueve las posiciones de las otras.
    for e in sorted(ediciones, key=lambda e: e.desde, reverse=True):
        fuente = fuente[: e.desde] + e.texto + fuente[e.hasta :]
    _arbol(fuente)  # tiene que seguir compilando
    return fuente


# ---------------------------------------------------------------------------------
# cache_control
# ---------------------------------------------------------------------------------


def _llamada(arbol: ast.Module, linea: int) -> ast.Call:
    """La llamada al modelo que cubre la línea anotada: la más interior con `model` o
    `messages`, que son los argumentos de cualquier `create` de un proveedor."""
    candidatas = [
        n
        for n in ast.walk(arbol)
        if isinstance(n, ast.Call)
        and n.lineno <= linea <= (n.end_lineno or n.lineno)
        and any(k.arg in ("model", "messages") for k in n.keywords)
    ]
    if not candidatas:
        raise SinPropuesta("pr.sin_llamada", linea=linea)
    return min(candidatas, key=lambda n: ((n.end_lineno or n.lineno) - n.lineno, -n.lineno))


def _tiene_cache(nodo: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Dict)
        and any(isinstance(k, ast.Constant) and k.value == "cache_control" for k in n.keys)
        for n in ast.walk(nodo)
    )


def _asignacion(arbol: ast.Module, llamada: ast.Call, nombre: str) -> ast.AST | None:
    """El valor asignado a `nombre`, si se asigna una sola vez en la función de la
    llamada o, si ahí no, en el módulo. Con varias asignaciones no se elige una."""
    ambitos: list[ast.AST] = [
        f
        for f in ast.walk(arbol)
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
        and f.lineno <= llamada.lineno <= (f.end_lineno or f.lineno)
    ]
    ambitos.sort(key=lambda f: f.lineno, reverse=True)  # la más interior primero
    ambitos.append(arbol)
    for ambito in ambitos:
        # En una función, cualquier asignación dentro; en el módulo, sólo las de arriba.
        nodos = arbol.body if isinstance(ambito, ast.Module) else list(ast.walk(ambito))
        valores = [
            n.value
            for n in nodos
            if isinstance(n, (ast.Assign, ast.AnnAssign))
            and n.value is not None
            and any(
                isinstance(t, ast.Name) and t.id == nombre
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
            )
        ]
        if len(valores) == 1:
            return valores[0]
        if valores:
            return None
    return None


def _es_texto(nodo: ast.AST, arbol: ast.Module, llamada: ast.Call, hondo: int = 0) -> bool:
    if hondo > 4:
        return False
    if isinstance(nodo, ast.Constant):
        return isinstance(nodo.value, str)
    if isinstance(nodo, ast.JoinedStr):
        return True
    if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, (ast.Add, ast.Mod)):
        return _es_texto(nodo.left, arbol, llamada, hondo + 1) or (
            isinstance(nodo.op, ast.Add) and _es_texto(nodo.right, arbol, llamada, hondo + 1)
        )
    if isinstance(nodo, ast.Call):
        if isinstance(nodo.func, ast.Name) and nodo.func.id == "str":
            return True
        return isinstance(nodo.func, ast.Attribute) and nodo.func.attr in _DEVUELVEN_TEXTO
    if isinstance(nodo, ast.Name):
        valor = _asignacion(arbol, llamada, nodo.id)
        return valor is not None and _es_texto(valor, arbol, llamada, hondo + 1)
    return False


def _con_cache_al_final(lista: ast.List, texto: _Texto) -> _Edicion:
    """Añade `cache_control` al último bloque de una lista de bloques literal."""
    if not lista.elts or not isinstance(lista.elts[-1], ast.Dict):
        raise SinPropuesta("pr.system_desconocido")
    ultimo = lista.elts[-1]
    if not ultimo.values or any(k is None for k in ultimo.keys):
        raise SinPropuesta("pr.system_desconocido")  # vacío, o con `**otro` dentro
    # Justo detrás del último valor: vale con coma final y con el bloque en varias líneas.
    fin = texto.hasta(ultimo.values[-1])
    return _Edicion(fin, fin, f", {CACHE_CONTROL}")


def poner_cache(fuente: str, linea: int) -> str:
    """El fichero con el `system` de la llamada de `linea` marcado para la caché de
    prompts de Anthropic (`cache_control: ephemeral` en su último bloque)."""
    arbol = _arbol(fuente)
    texto = _Texto(fuente)
    llamada = _llamada(arbol, linea)
    if any(k.arg == "cache_control" for k in llamada.keywords) or _tiene_cache(llamada):
        raise SinPropuesta("pr.ya_cachea")
    system = next((k for k in llamada.keywords if k.arg == "system"), None)
    if system is None:
        raise SinPropuesta("pr.sin_system")
    valor = system.value

    if isinstance(valor, ast.List):
        return _aplicar(fuente, [_con_cache_al_final(valor, texto)])
    if isinstance(valor, ast.Name):
        definido = _asignacion(arbol, llamada, valor.id)
        if isinstance(definido, ast.List):
            if _tiene_cache(definido):
                raise SinPropuesta("pr.ya_cachea")
            return _aplicar(fuente, [_con_cache_al_final(definido, texto)])
    if not _es_texto(valor, arbol, llamada):
        raise SinPropuesta("pr.system_desconocido")
    bloque = (
        f'[{{"type": "text", "text": {texto.segmento(valor)}, {CACHE_CONTROL}}}]'
    )
    return _aplicar(fuente, [_Edicion(texto.desde(valor), texto.hasta(valor), bloque)])


# ---------------------------------------------------------------------------------
# Tope de vueltas
# ---------------------------------------------------------------------------------


def _funcion(arbol: ast.Module, linea: int) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """La función cuya cabecera (decoradores y `def`) incluye la línea anotada.

    El SDK anota la primera línea del código de la función, que según la versión de
    Python es la del `def` o la del primer decorador: sirven las dos.
    """
    for n in ast.walk(arbol):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            primera = min([d.lineno for d in n.decorator_list] + [n.lineno])
            if primera <= linea <= n.lineno:
                return n
    raise SinPropuesta("pr.sin_funcion", linea=linea)


def _es_generador(funcion: ast.AST) -> bool:
    pendientes = list(ast.iter_child_nodes(funcion))
    while pendientes:
        n = pendientes.pop()
        if isinstance(n, (ast.Yield, ast.YieldFrom)):
            return True
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue  # lo de dentro de otra función no hace generadora a ésta
        pendientes.extend(ast.iter_child_nodes(n))
    return False


def _linea_del_import(arbol: ast.Module) -> int | None:
    """Dónde va `import laplace`: antes del primer import que no sea de `__future__`.
    `None` si ya está."""
    for n in arbol.body:
        if isinstance(n, ast.Import) and any(
            a.name == "laplace" and a.asname is None for a in n.names
        ):
            return None
    for n in arbol.body:
        if isinstance(n, ast.Import) or (
            isinstance(n, ast.ImportFrom) and n.module != "__future__"
        ):
            return n.lineno
    # Sin imports: después del docstring y de los `__future__`, o al principio.
    ultima = 0
    for n in arbol.body:
        es_doc = isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
        if es_doc or (isinstance(n, ast.ImportFrom) and n.module == "__future__"):
            ultima = n.end_lineno or n.lineno
        else:
            break
    return ultima + 1


def poner_tope(fuente: str, linea: int, max_loop: int) -> str:
    """El fichero con `@laplace.guard(max_loop=…)` en la función de `linea`.

    Cada llamada a la función es una ejecución nueva (D-184): el tope corta el paso que
    se repite sin avanzar dentro de esa llamada, que es lo que señaló el Diagnóstico.
    """
    arbol = _arbol(fuente)
    texto = _Texto(fuente)
    funcion = _funcion(arbol, linea)
    if any("guard" in texto.segmento(d) for d in funcion.decorator_list):
        raise SinPropuesta("pr.ya_tiene_tope")
    if _es_generador(funcion):
        raise SinPropuesta("pr.tope_generador")
    primera = min([d.lineno for d in funcion.decorator_list] + [funcion.lineno])
    linea_texto = texto.lineas[primera - 1]
    sangria = linea_texto[: len(linea_texto) - len(linea_texto.lstrip())]
    ediciones = [
        _Edicion(
            texto.inicios[primera - 1],
            texto.inicios[primera - 1],
            f"{sangria}@laplace.guard(max_loop={int(max_loop)})\n",
        )
    ]
    donde = _linea_del_import(arbol)
    if donde is not None:
        inicio = texto.inicios[min(donde, len(texto.lineas)) - 1] if texto.lineas else 0
        ediciones.append(_Edicion(inicio, inicio, "import laplace\n"))
    return _aplicar(fuente, ediciones)
