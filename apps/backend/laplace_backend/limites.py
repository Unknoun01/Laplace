"""Tope al tamaño del cuerpo de las peticiones, y cabeceras de seguridad.

Ni Uvicorn ni Starlette ponen uno, y aquí importa: la ingesta acepta lotes de spans de
cualquier clave de cualquier proyecto, el middleware de autenticación lee el cuerpo de
las escrituras para comprobar el proyecto, y todo eso vive en un proceso que comparten
todos los clientes. Una sola petición enorme lo llenaba.

Va como middleware ASGI puro, y el más externo de los nuestros, para cortar antes de que
nadie haya leído nada: primero por `Content-Length`, y si no viene —cuerpo por trozos—,
contando lo que va llegando.
"""

from __future__ import annotations

import json
from typing import Any


class _Demasiado(Exception):
    pass


async def _responder_413(send: Any, maximo: int) -> None:
    cuerpo = json.dumps(
        {"detail": f"la petición pasa del tope de {maximo // (1024 * 1024)} MiB"},
        ensure_ascii=False,
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(cuerpo)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": cuerpo})


class LimiteCuerpo:
    def __init__(self, app: Any, maximo: int) -> None:
        self.app = app
        self.maximo = maximo

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for nombre, valor in scope.get("headers", []):
            if nombre == b"content-length":
                try:
                    if int(valor) > self.maximo:
                        await _responder_413(send, self.maximo)
                        return
                except ValueError:
                    pass

        leidos = 0
        empezada = False

        async def recibir() -> Any:
            nonlocal leidos
            mensaje = await receive()
            if mensaje["type"] == "http.request":
                leidos += len(mensaje.get("body", b""))
                if leidos > self.maximo:
                    raise _Demasiado
            return mensaje

        async def enviar(mensaje: Any) -> None:
            nonlocal empezada
            if mensaje["type"] == "http.response.start":
                empezada = True
            await send(mensaje)

        try:
            await self.app(scope, recibir, enviar)
        except _Demasiado:
            if not empezada:
                await _responder_413(send, self.maximo)


#: Cabeceras de seguridad de toda respuesta, API e interfaz local. Ninguna cambia lo que
#: se ve: dicen al navegador que no adivine tipos, que no deje meter la página en un
#: iframe ajeno —las pantallas de borrar un proyecto o crear claves son un blanco de
#: clickjacking— y que no mande la URL entera a otros sitios.
CABECERAS_SEGURIDAD = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
)


class CabecerasSeguridad:
    """Añade `CABECERAS_SEGURIDAD` a cada respuesta que no las traiga ya."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def enviar(mensaje: Any) -> None:
            if mensaje["type"] == "http.response.start":
                puestas = {nombre.lower() for nombre, _ in mensaje.get("headers", [])}
                extra = [(n, v) for n, v in CABECERAS_SEGURIDAD if n not in puestas]
                mensaje = {**mensaje, "headers": [*mensaje.get("headers", []), *extra]}
            await send(mensaje)

        await self.app(scope, receive, enviar)
