"""LLM-as-judge: el veredicto de máquina, con su coste medido.

Es **opcional** y está apagado por defecto. Cuando se enciende, mira una traza y
responde a las dos preguntas que se pueden responder leyendo la ejecución: ¿hizo lo que
se le pidió? ¿se inventó algo? Y nada más: no puntúa estilo ni opina sobre el prompt.

Dos condiciones que no son negociables:

* **Su veredicto nunca se mezcla con el de una persona.** Se guarda con `source` de
  máquina y con su propio bloque de coste, que una anotación humana no puede tener. La
  separación es estructural, no una etiqueta (D-083).
* **Lo que cuesta juzgar es coste real.** Se mide con la misma tabla de precios con la
  que medimos el gasto del usuario y se guarda en la anotación. Si no se registrara,
  sabríamos menos de nuestro propio gasto que del ajeno, que sería un chiste malo en un
  producto que se vende como «te digo lo que cuesta tu agente» (D-088).

Sin dependencias nuevas: un POST con `urllib`, igual que el aviso a Slack (D-068). Si el
modelo del juez no está en la tabla de precios, su coste es «no lo sabemos» y se dice,
que es la misma regla que aplicamos a las trazas ajenas (D-043).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from laplace.schema import Annotation, JudgeRun, Span

from .config import Settings
from .pricing import get_price_table
from .storage.metadata import new_id
from .tree import trace_io

logger = logging.getLogger("laplace.judge")

#: Versión del prompt. Dos veredictos emitidos con prompts distintos no son comparables
#: entre sí, y sin esto no habría manera de saberlo tres semanas después.
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """\
Eres un evaluador de ejecuciones de agentes de IA. Recibes lo que se le pidió al agente
y lo que respondió. Respondes ÚNICAMENTE con un objeto JSON, sin texto alrededor:

{"verdict": "pass" | "fail", "reason": "<una frase>"}

Criterios, sólo estos dos:
1. ¿Hizo lo que se le pedía? Si la respuesta no atiende a la petición, es "fail".
2. ¿Se inventó algo? Si afirma hechos que no salen de su entrada ni de sus herramientas,
   es "fail".

No juzgues el estilo, la longitud ni el tono. Si no tienes información suficiente para
decidir, responde "fail" y dilo en la razón: es preferible marcar de más y que una
persona lo revise, a dar por bueno lo que no se ha podido comprobar.
"""

#: Tope de caracteres por campo. Una traza con un manual entero dentro convertiría cada
#: veredicto en una llamada carísima, y es justo el tipo de gasto que este producto
#: existe para evitar.
MAX_CHARS = 4000


@dataclass
class JudgeConfig:
    enabled: bool = False
    system: str = "anthropic"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    max_output_tokens: int = 200

    @classmethod
    def of(cls, settings: Settings) -> JudgeConfig:
        return cls(
            enabled=settings.evals_judge_enabled and bool(settings.evals_judge_api_key),
            system=settings.evals_judge_system,
            model=settings.evals_judge_model,
            api_key=settings.evals_judge_api_key,
            base_url=settings.evals_judge_base_url,
        )


class JudgeUnavailable(RuntimeError):
    """El juez no está configurado, o el proveedor no ha respondido."""


def _clip(value: Any) -> str:
    texto = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if texto is None:
        return ""
    return texto[:MAX_CHARS] + (" …[recortado]" if len(texto) > MAX_CHARS else "")


def build_prompt(spans: list[Span], expected: Any = None) -> str:
    """El texto que se le manda al juez. Se enseña tal cual en modo avanzado.

    Por el mismo motivo que la consulta de un hallazgo (D-067): un veredicto que no se
    puede auditar no se puede discutir, y el primer «este fail está mal» llega el día
    uno.
    """
    entrada, salida = trace_io(spans)
    partes = [
        "### Lo que se le pidió al agente",
        _clip(entrada) or "(no se capturó la entrada)",
        "",
        "### Lo que respondió",
        _clip(salida) or "(no se capturó la salida)",
    ]
    if expected is not None:
        partes += [
            "",
            "### Lo que respondió la versión de referencia",
            _clip(expected),
            "",
            "La referencia es lo que había antes, no una verdad certificada: úsala como "
            "contexto, no como solución.",
        ]
    return "\n".join(partes)


# ---------------------------------------------------------------------------------
# Llamada al proveedor
# ---------------------------------------------------------------------------------


def _post(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    peticion = urllib.request.Request(  # noqa: S310 - el esquema se valida abajo
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    if not url.startswith("https://"):
        raise JudgeUnavailable("el endpoint del juez tiene que ser https")
    try:
        with urllib.request.urlopen(peticion, timeout=60) as respuesta:  # noqa: S310
            return json.loads(respuesta.read())
    except urllib.error.URLError as exc:
        raise JudgeUnavailable(f"el proveedor del juez no ha respondido: {exc}") from exc


def _call(config: JudgeConfig, prompt: str) -> tuple[str, int, int]:
    """Devuelve (texto, tokens de entrada, tokens de salida)."""
    if config.system == "anthropic":
        base = config.base_url or "https://api.anthropic.com"
        datos = _post(
            f"{base}/v1/messages",
            {
                "model": config.model,
                "max_tokens": config.max_output_tokens,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            },
            {"x-api-key": config.api_key, "anthropic-version": "2023-06-01"},
        )
        texto = "".join(b.get("text", "") for b in datos.get("content", []))
        uso = datos.get("usage", {})
        return texto, int(uso.get("input_tokens", 0)), int(uso.get("output_tokens", 0))

    base = config.base_url or "https://api.openai.com"
    datos = _post(
        f"{base}/v1/chat/completions",
        {
            "model": config.model,
            "max_completion_tokens": config.max_output_tokens,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
        {"Authorization": f"Bearer {config.api_key}"},
    )
    texto = datos["choices"][0]["message"]["content"] or ""
    uso = datos.get("usage", {})
    return texto, int(uso.get("prompt_tokens", 0)), int(uso.get("completion_tokens", 0))


def _parse(texto: str) -> tuple[str, str]:
    """Veredicto y razón a partir de la respuesta.

    Si el modelo devuelve algo que no es el JSON pedido, el veredicto es `unknown` y no
    `fail`: «no he entendido al juez» y «el agente lo hizo mal» son cosas distintas, y
    confundirlas metería en el acierto un fallo que es nuestro.
    """
    recorte = texto.strip()
    if recorte.startswith("```"):
        recorte = recorte.strip("`")
        recorte = recorte.partition("\n")[2] if "\n" in recorte else recorte
    inicio, fin = recorte.find("{"), recorte.rfind("}")
    if inicio < 0 or fin <= inicio:
        return "unknown", f"el juez no devolvió JSON: {texto[:160]}"
    try:
        datos = json.loads(recorte[inicio : fin + 1])
    except ValueError:
        return "unknown", f"el juez devolvió un JSON ilegible: {texto[:160]}"
    veredicto = str(datos.get("verdict", "")).lower()
    if veredicto not in ("pass", "fail"):
        return "unknown", f"veredicto no reconocido: {datos.get('verdict')!r}"
    return veredicto, str(datos.get("reason", ""))[:500]


def judge_trace(
    config: JudgeConfig, trace_id: str, spans: list[Span], expected: Any = None
) -> Annotation:
    """Juzga una traza y devuelve la anotación de máquina, con su coste dentro."""
    if not config.enabled:
        raise JudgeUnavailable(
            "el juez no está configurado: hacen falta LAPLACE_EVALS_JUDGE_ENABLED y "
            "LAPLACE_EVALS_JUDGE_API_KEY"
        )
    if not spans:
        raise JudgeUnavailable(f"la traza {trace_id} no tiene pasos que juzgar")

    prompt = build_prompt(spans, expected)
    texto, tok_in, tok_out = _call(config, prompt)
    veredicto, razon = _parse(texto)

    coste = get_price_table().compute(
        config.model, input_tokens=tok_in, output_tokens=tok_out
    )
    return Annotation(
        id=new_id("an"),
        trace_id=trace_id,
        source="llm_judge",
        verdict=veredicto,
        comment=razon,
        # El autor de una anotación de máquina es el modelo, no una persona. Sirve
        # además de clave de unicidad: volver a juzgar con el mismo modelo sustituye.
        author=config.model,
        created_at=datetime.now(timezone.utc),
        judge=JudgeRun(
            model=config.model,
            input_tokens=tok_in,
            output_tokens=tok_out,
            cost_usd=coste.total_usd,
            cost_unknown=coste.unknown,
            prompt_version=PROMPT_VERSION,
        ),
    )
