"""Detección del modelo local con el que corren las pruebas sin coste.

Un servidor de modelos local —Ollama— expone la API de OpenAI en
`http://localhost:11434/v1`. Pasándole al cliente **real** de OpenAI ese `base_url` y
una clave cualquiera, el SDK de verdad habla con un modelo de verdad: se recorre el
cliente, el parche, el parseo, el lector de SSE, la creación del span y la ingesta,
esta vez **con red y con un servidor al otro lado**. Sin gastar un céntimo.

Módulo normal y no `conftest.py` por lo mismo que `helpers.py`: los tests necesitan
importar estas funciones, no sólo recibir fixtures.

Las sondas son de sesión y se cachean: cada una cuesta una petición HTTP, y una de
ellas una generación de un token. Preguntarlo por test multiplicaría eso por veinte.
"""

from __future__ import annotations

import functools
import os

import pytest

#: Dónde escucha el servidor local. Se puede mover: Ollama admite `OLLAMA_HOST`, y LM
#: Studio sirve en el 1234. Es lo único que hay que cambiar para usar otro.
BASE_URL = os.getenv("LAPLACE_LOCAL_BASE_URL", "http://localhost:11434/v1")

#: El servidor local no mira la clave, pero el SDK de OpenAI se niega a construirse sin
#: una. Que se lea de un tirón lo que es: falsa a propósito, y no un descuido.
CLAVE_FICTICIA = "laplace-clave-ficticia-no-se-usa"

#: Preferencia de modelo cuando el servidor tiene varios cargados. Son los pequeños que
#: corren en un portátil sin GPU; la lista es una preferencia, no un requisito: si no
#: hay ninguno, se coge el primero que el servidor ofrezca.
PREFERIDOS = (
    "qwen2.5:0.5b",
    "qwen2.5:1.5b",
    "llama3.2:1b",
    "gemma3:1b",
    "smollm2:360m",
)

#: Segundos de espera. Un modelo pequeño en CPU tarda: la primera llamada carga pesos a
#: memoria y puede irse a más de un minuto. Corto para la sonda —si no hay nada
#: escuchando, se sabe enseguida— y generoso para generar.
TIMEOUT_SONDA = 3.0
TIMEOUT_GENERACION = 120.0


@functools.lru_cache(maxsize=1)
def _catalogo() -> tuple[str, ...] | None:
    """Los modelos que el servidor local dice tener, o `None` si no hay servidor.

    Se pregunta por `GET /v1/models`, que es parte de la API de OpenAI y no una
    extensión de Ollama: así esto sigue valiendo para cualquier servidor compatible.
    """
    import httpx

    try:
        respuesta = httpx.get(f"{BASE_URL}/models", timeout=TIMEOUT_SONDA)
        respuesta.raise_for_status()
        datos = respuesta.json().get("data") or []
    except Exception:  # noqa: BLE001 - no hay servidor: no es un error, es un salto
        return None
    return tuple(str(m.get("id")) for m in datos if m.get("id"))


def disponible() -> bool:
    return _catalogo() is not None


@functools.lru_cache(maxsize=1)
def modelo() -> str | None:
    """El modelo con el que llamar, o `None` si no hay ninguno utilizable.

    Se descubre en vez de escribirse a mano: así vale con el que el usuario se haya
    descargado, y no hay un nombre de modelo en el código que caduque como caducaron
    los de los fixtures (D-100).
    """
    forzado = os.getenv("LAPLACE_LOCAL_MODEL")
    if forzado:
        return forzado
    catalogo = _catalogo()
    if not catalogo:
        return None
    for preferido in PREFERIDOS:
        if preferido in catalogo:
            return preferido
    return catalogo[0]


def _motivo() -> str | None:
    """Por qué no se puede correr contra el modelo local, si no se puede."""
    if not disponible():
        return (
            f"no hay servidor de modelos local escuchando en {BASE_URL}. "
            "Arráncalo con `ollama serve` (ver docs/tests-con-modelo-local.md) "
            "o apunta a otro con LAPLACE_LOCAL_BASE_URL."
        )
    if not modelo():
        return (
            f"el servidor de {BASE_URL} no tiene ningún modelo. "
            "Descarga uno con `ollama pull qwen2.5:0.5b`."
        )
    return None


def salta_sin_servidor(fn):
    """Marca que salta la prueba cuando no hay modelo local, diciendo qué hacer.

    Se evalúa al recoger los tests, así que la sonda se hace una vez por sesión y no
    una por prueba.
    """
    salta = pytest.mark.skipif(_motivo() is not None, reason=_motivo() or "")
    return pytest.mark.modelo_local(salta(fn))


def cliente(*, base_url: str | None = None, transport=None, timeout: float | None = None):
    """El cliente **real** de OpenAI, apuntando al servidor local.

    Lo único que se le cambia es a dónde llama y con qué clave. Todo lo demás —firma,
    validación, parseo, reintentos, lector de SSE— es el del SDK publicado.
    """
    import httpx
    import openai

    kwargs = {
        "api_key": CLAVE_FICTICIA,
        "base_url": base_url or BASE_URL,
        "timeout": timeout or TIMEOUT_GENERACION,
        # Un reintento automático convertiría un fallo del servidor en dos spans y la
        # prueba de errores diría otra cosa de la que cree.
        "max_retries": 0,
    }
    if transport is not None:
        kwargs["http_client"] = httpx.Client(transport=transport, timeout=kwargs["timeout"])
    return openai.OpenAI(**kwargs)


def cliente_async(*, base_url: str | None = None):
    """El mismo, asíncrono. El parche de Laplace envuelve dos clases distintas
    —`Completions` y `AsyncCompletions`— y sólo una de las dos se usa en el resto de
    las pruebas, así que la otra podría romperse sin que nadie se enterara."""
    import openai

    return openai.AsyncOpenAI(
        api_key=CLAVE_FICTICIA,
        base_url=base_url or BASE_URL,
        timeout=TIMEOUT_GENERACION,
        max_retries=0,
    )


@functools.lru_cache(maxsize=1)
def reporta_usage_en_streaming() -> bool:
    """Si el servidor local manda el recuento de tokens al final de un stream.

    `stream_options={"include_usage": True}` es de la API de OpenAI y los servidores
    locales tardaron en implementarlo. Se comprueba de verdad en vez de suponerlo por
    número de versión, y cuesta una generación de un token.

    Importa porque separa dos pruebas distintas: con recuento el span tiene que salir
    medido, y sin recuento tiene que salir **estimado y marcado como tal**. Suponerlo
    haría que una de las dos midiera otra cosa sin avisar.
    """
    if not disponible() or not modelo():
        return False
    try:
        trozos = list(
            cliente().chat.completions.create(
                model=modelo(),
                messages=[{"role": "user", "content": "hola"}],
                max_tokens=1,
                stream=True,
                stream_options={"include_usage": True},
            )
        )
    except Exception:  # noqa: BLE001
        return False
    return any(getattr(t, "usage", None) for t in trozos)
