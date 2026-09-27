"""En qué idioma se habla en esta petición (Fase 5, D-147).

El motor redacta: títulos de hallazgos, la lectura del Panel, los avisos. Pasar el idioma
de mano en mano por cada función que acaba escribiendo una frase habría tocado medio
backend y dejado siempre un sitio sin pasarlo. Va en una variable de contexto, que el
middleware rellena al llegar la petición y que `cifras` y `textos` leen al escribir.

Una variable de contexto sigue a la petición por los hilos del pool (Starlette copia el
contexto en `run_in_threadpool`) y por `asyncio.to_thread`. Lo que se ejecuta **más
tarde** y fuera de la petición —el renovador del Diagnóstico, un aviso programado— tiene
que fijarlo él con `usar()`: no hereda el de nadie.

Sin idioma pedido, español: es el idioma en el que nació la API, y quien la usa desde un
script sin cabeceras sigue recibiendo lo mismo que ayer.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal
from urllib.parse import parse_qs

Idioma = Literal["es", "en", "pt", "fr", "zh"]

#: Los cinco, en el orden en que se ofrecen.
IDIOMAS: tuple[Idioma, ...] = ("es", "en", "pt", "fr", "zh")
POR_DEFECTO: Idioma = "es"

_actual: ContextVar[Idioma] = ContextVar("laplace_idioma", default=POR_DEFECTO)


def actual() -> Idioma:
    return _actual.get()


def valido(idioma: str | None) -> Idioma | None:
    """El idioma si es uno de los cinco (`pt-BR` cuenta como `pt`), o `None`."""
    if not idioma:
        return None
    base = idioma.strip().lower().replace("_", "-").split("-")[0]
    return base if base in IDIOMAS else None  # type: ignore[return-value]


@contextmanager
def usar(idioma: str | None) -> Iterator[Idioma]:
    """Habla en `idioma` dentro del bloque. Uno desconocido deja el de por defecto."""
    elegido = valido(idioma) or POR_DEFECTO
    marca = _actual.set(elegido)
    try:
        yield elegido
    finally:
        _actual.reset(marca)


def fijar(idioma: str | None) -> Idioma:
    """Como `usar`, sin bloque: para el middleware, cuyo contexto muere con la petición."""
    elegido = valido(idioma) or POR_DEFECTO
    _actual.set(elegido)
    return elegido


def negociar(cabecera: str | None) -> Idioma:
    """El mejor de los cinco para una cabecera `Accept-Language`.

    Se respetan los pesos (`q`); a igual peso, el orden en que vienen. Si ninguno de los
    pedidos se habla, el de por defecto: una cabecera `de-DE` no puede dejar la página
    sin idioma.
    """
    if not cabecera:
        return POR_DEFECTO
    candidatos: list[tuple[float, int, Idioma]] = []
    for orden, trozo in enumerate(cabecera.split(",")):
        etiqueta, _, resto = trozo.strip().partition(";")
        peso = 1.0
        if resto.strip().startswith("q="):
            try:
                peso = float(resto.strip()[2:])
            except ValueError:
                peso = 0.0
        idioma = valido(etiqueta)
        if idioma and peso > 0:
            candidatos.append((-peso, orden, idioma))
    return min(candidatos)[2] if candidatos else POR_DEFECTO


class MiddlewareIdioma:
    """Fija el idioma de cada petición y lo dice en la respuesta.

    ASGI puro, no `@app.middleware`: la variable de contexto se fija y se restaura en el
    mismo sitio, y llega al endpoint sin depender de cómo el middleware de Starlette
    reparte tareas. Manda `?lang=` si viene —un enlace de un correo tiene que abrir en el
    idioma del correo— y si no, `Accept-Language`.
    """

    def __init__(self, app) -> None:  # noqa: ANN001 - una aplicación ASGI
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        pedido = parse_qs(scope.get("query_string", b"").decode("latin-1")).get("lang", [None])[0]
        cabecera = dict(scope.get("headers") or []).get(b"accept-language", b"").decode("latin-1")
        elegido = valido(pedido) or negociar(cabecera)
        marca = _actual.set(elegido)

        async def enviar(mensaje) -> None:  # noqa: ANN001
            if mensaje["type"] == "http.response.start":
                cabeceras = list(mensaje.get("headers") or [])
                cabeceras.append((b"content-language", elegido.encode()))
                cabeceras.append((b"vary", b"Accept-Language"))
                mensaje = {**mensaje, "headers": cabeceras}
            await send(mensaje)

        try:
            await self.app(scope, receive, enviar)
        finally:
            _actual.reset(marca)


__all__ = [
    "IDIOMAS",
    "POR_DEFECTO",
    "Idioma",
    "MiddlewareIdioma",
    "actual",
    "fijar",
    "negociar",
    "usar",
    "valido",
]
