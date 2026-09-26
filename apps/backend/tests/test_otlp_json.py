"""OTLP/JSON tal y como lo manda un exportador de verdad.

El SDK de Python manda protobuf, y el JSON se aceptaba «para depurar con curl». Pero el
exportador por defecto de OpenTelemetry para Node (`@opentelemetry/exporter-trace-otlp-
http`) manda JSON, y ahí la especificación dice que los ids de traza y de span van en
**hexadecimal**, no en base64 como los `bytes` del JSON genérico de protobuf. Un id
hexadecimal de 32 caracteres también es base64 válido, así que se leía sin error y se
guardaba **otro** id: cada traza de un agente en TypeScript llegaba con ids inventados.
"""

from __future__ import annotations

import json

from laplace_backend.ingest.otlp import decode_request, parse_spans

TRAZA = "5b8efff798038103d269b633813fc60c"
RAIZ = "eee19b7ec3c1b174"
HIJO = "eee19b7ec3c1b173"


def _cuerpo(**cambios) -> bytes:
    raiz = {
        "traceId": TRAZA,
        "spanId": RAIZ,
        "name": "atender",
        "kind": 1,
        "startTimeUnixNano": "1770000000000000000",
        "endTimeUnixNano": "1770000002000000000",
        "status": {"code": 1},
    }
    hijo = {
        "traceId": TRAZA,
        "spanId": HIJO,
        "parentSpanId": RAIZ,
        "name": "chat gpt-5.6-luna",
        "kind": 3,
        "startTimeUnixNano": "1770000000500000000",
        "endTimeUnixNano": "1770000001500000000",
        "attributes": [
            {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
            {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
            {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-5.6-luna"}},
            {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "1200"}},
            {"key": "gen_ai.usage.output_tokens", "value": {"intValue": 12}},
        ],
        "status": {},
    }
    hijo.update(cambios)
    cuerpo = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "agente-ts"}}
                    ]
                },
                "scopeSpans": [
                    {"scope": {"name": "@arizeai/openinference"}, "spans": [raiz, hijo]}
                ],
            }
        ]
    }
    return json.dumps(cuerpo).encode()


def _spans(cuerpo: bytes):
    return parse_spans(decode_request(cuerpo, content_type="application/json"))


def test_los_ids_hexadecimales_se_leen_como_hexadecimales():
    spans = {s.name: s for s in _spans(_cuerpo())}
    assert spans["atender"].trace_id == TRAZA
    assert spans["atender"].span_id == RAIZ
    assert spans["atender"].parent_span_id is None
    hijo = spans["chat gpt-5.6-luna"]
    assert hijo.trace_id == TRAZA
    assert hijo.span_id == HIJO
    assert hijo.parent_span_id == RAIZ, "sin esto el árbol de la traza se deshace"


def test_el_span_de_llm_llega_entero():
    hijo = next(s for s in _spans(_cuerpo()) if s.type == "llm")
    assert hijo.project_id == "agente-ts"
    assert hijo.llm.usage.input_tokens == 1200
    assert hijo.llm.usage.output_tokens == 12
    assert hijo.llm.cost.total_usd > 0
    assert hijo.duration_ms == 1000


def test_los_ids_en_base64_siguen_valiendo():
    """Quien depuraba con curl copiando el JSON de protobuf mandaba base64: no se rompe.
    Se distingue por la forma: un id hexadecimal tiene 32 o 16 caracteres hexadecimales,
    y el base64 de 16 u 8 bytes tiene 24 o 12 y casi nunca es sólo hexadecimal."""
    import base64

    b64_traza = base64.b64encode(bytes.fromhex(TRAZA)).decode()
    b64_hijo = base64.b64encode(bytes.fromhex(HIJO)).decode()
    b64_raiz = base64.b64encode(bytes.fromhex(RAIZ)).decode()
    cuerpo = json.loads(_cuerpo())
    for span in cuerpo["resourceSpans"][0]["scopeSpans"][0]["spans"]:
        span["traceId"] = b64_traza
        span["spanId"] = b64_hijo if span["spanId"] == HIJO else b64_raiz
        if span.get("parentSpanId"):
            span["parentSpanId"] = b64_raiz
    spans = {s.name: s for s in _spans(json.dumps(cuerpo).encode())}
    assert spans["chat gpt-5.6-luna"].trace_id == TRAZA
    assert spans["chat gpt-5.6-luna"].parent_span_id == RAIZ


def test_los_enlaces_tambien_llevan_ids_hexadecimales():
    """Los `links` de un span llevan su propio traceId y spanId, con la misma regla."""
    cuerpo = _cuerpo(links=[{"traceId": TRAZA, "spanId": RAIZ}])
    hijo = next(s for s in _spans(cuerpo) if s.type == "llm")
    assert hijo.trace_id == TRAZA
