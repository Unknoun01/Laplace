"""Traducción de OTLP a nuestro contrato de traza.

El endpoint es OTLP estándar, así que cualquier proceso ya instrumentado con
OpenTelemetry puede exportar a Laplace sin usar nuestro SDK. Aquí se hacen tres cosas
que el estándar no hace y que el producto necesita:

1. Clasificar el span en uno de los cinco tipos del contrato.
2. Calcular el coste desglosado (D-005).
3. Calcular el `dedup_hash` que permite detectar repeticiones (D-007).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import re
import zlib
from datetime import datetime, timezone
from typing import Any

from laplace import semconv
from laplace.integrations._streaming import estimate_messages_tokens
from laplace.schema import (
    Cost,
    LLMAttributes,
    RetrievalAttributes,
    Span,
    SpanEvent,
    TokenUsage,
    ToolAttributes,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from ..pricing import get_price_table
from ..textos import t
from .convenciones import ENTRADAS_DEL_PADRE, TRADUCIDOS, mensajes_del_padre, normalizar

logger = logging.getLogger("laplace.ingest")

_STATUS_BY_CODE = {0: "unset", 1: "ok", 2: "error"}


# ---------------------------------------------------------------------------------
# Decodificación del sobre OTLP
# ---------------------------------------------------------------------------------


#: Cuánto puede crecer un cuerpo al descomprimirlo, respecto al tope del cable. Las
#: trazas comprimen bien (texto repetido), pero no mil veces: eso es una bomba.
OTLP_EXPANSION = 4


class CuerpoDemasiadoGrande(ValueError):
    """El cuerpo, descomprimido, pasa del tope. La ruta lo convierte en un 413."""


def _gunzip(body: bytes, maximo: int) -> bytes:
    """`gzip.decompress` con tope: nunca materializa más de `maximo` bytes.

    `gzip.decompress` a secas descomprime lo que haga falta, y diez megas de ceros
    comprimidos son diez gigas en memoria: con cualquier clave de ingesta se tumbaba el
    proceso que comparten todos los proyectos.
    """
    descompresor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    salida = descompresor.decompress(body, maximo)
    if descompresor.unconsumed_tail:
        raise CuerpoDemasiadoGrande(t("error.cuerpo_grande_descomprimido", bytes=maximo))
    return salida + descompresor.flush()


#: Campos de id en OTLP/JSON, en las dos grafías que acepta el parser, con su largo en
#: bytes. La especificación los manda en hexadecimal; el JSON genérico de protobuf, en
#: base64. Por el largo no se confunden: 16 bytes son 32 caracteres en hexadecimal y 24
#: en base64, y 8 bytes son 16 y 12.
_CAMPOS_ID = {
    "traceId": 16,
    "trace_id": 16,
    "spanId": 8,
    "span_id": 8,
    "parentSpanId": 8,
    "parent_span_id": 8,
}
_HEX = re.compile(r"^[0-9a-fA-F]*$")


def _ids_a_base64(datos: Any) -> Any:
    """Pasa a base64 los ids que vienen en hexadecimal, como manda OTLP/JSON.

    Es lo que envía el exportador por defecto de OpenTelemetry para Node. Un id
    hexadecimal de 32 caracteres también es base64 válido, así que sin esto se leía sin
    error y se guardaba **otro** id: las trazas de un agente en TypeScript llegaban con
    ids inventados y el árbol deshecho.
    """
    if isinstance(datos, list):
        return [_ids_a_base64(x) for x in datos]
    if not isinstance(datos, dict):
        return datos
    salida = {}
    for clave, valor in datos.items():
        largo = _CAMPOS_ID.get(clave)
        if largo and isinstance(valor, str) and len(valor) == largo * 2 and _HEX.match(valor):
            valor = base64.b64encode(bytes.fromhex(valor)).decode("ascii")
        elif isinstance(valor, (dict, list)):
            valor = _ids_a_base64(valor)
        salida[clave] = valor
    return salida


def decode_request(
    body: bytes,
    content_type: str = "",
    content_encoding: str = "",
    max_bytes: int | None = None,
) -> ExportTraceServiceRequest:
    """Acepta protobuf (lo que emite el SDK) y JSON (cómodo para depurar con curl).

    `max_bytes` es el tope del cuerpo ya descomprimido; `None`, sin tope (pruebas).
    """
    if "gzip" in (content_encoding or "").lower():
        body = _gunzip(body, max_bytes) if max_bytes else gzip.decompress(body)
    if max_bytes and len(body) > max_bytes:
        raise CuerpoDemasiadoGrande(t("error.cuerpo_grande", bytes=max_bytes))

    request = ExportTraceServiceRequest()
    if "json" in (content_type or "").lower():
        from google.protobuf.json_format import ParseDict

        ParseDict(_ids_a_base64(json.loads(body.decode("utf-8"))), request)
    else:
        request.ParseFromString(body)
    return request


def _any_value(value: Any) -> Any:
    """Convierte un `AnyValue` de OTLP a un valor de Python."""
    which = value.WhichOneof("value")
    if which is None:
        return None
    if which == "string_value":
        return value.string_value
    if which == "bool_value":
        return value.bool_value
    if which == "int_value":
        return value.int_value
    if which == "double_value":
        return value.double_value
    if which == "bytes_value":
        return f"<bytes len={len(value.bytes_value)}>"
    if which == "array_value":
        return [_any_value(v) for v in value.array_value.values]
    if which == "kvlist_value":
        return {kv.key: _any_value(kv.value) for kv in value.kvlist_value.values}
    return None


def _attributes(pairs: Any) -> dict[str, Any]:
    return {kv.key: _any_value(kv.value) for kv in pairs}


def _ts(nanos: int) -> datetime:
    return datetime.fromtimestamp(nanos // 1_000_000_000, tz=timezone.utc).replace(
        microsecond=(nanos % 1_000_000_000) // 1000
    )


def _json_or(value: Any, fallback: Any) -> Any:
    if value is None or value == "":
        return fallback
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def _as_message_list(value: Any) -> list[dict[str, Any]]:
    """Los mensajes se guardan en crudo; lo que no sea una lista de dicts se envuelve."""
    parsed = _json_or(value, None)
    if parsed is None:
        return []
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item if isinstance(item, dict) else {"content": item} for item in parsed]
    return [{"content": parsed}]


# ---------------------------------------------------------------------------------
# Clasificación
# ---------------------------------------------------------------------------------


def classify(attrs: dict[str, Any]) -> str:
    """Tipo del span. Explícito si el SDK lo puso; si no, inferido de los atributos GenAI."""
    declared = attrs.get(semconv.LAPLACE_SPAN_TYPE)
    if declared in semconv.SPAN_TYPES:
        return str(declared)

    operation = attrs.get(semconv.GEN_AI_OPERATION_NAME)
    if operation == semconv.OPERATION_EXECUTE_TOOL or attrs.get(semconv.GEN_AI_TOOL_NAME):
        return semconv.SPAN_TYPE_TOOL
    if operation == semconv.OPERATION_INVOKE_AGENT:
        return semconv.SPAN_TYPE_AGENT
    if attrs.get(semconv.GEN_AI_REQUEST_MODEL) or attrs.get(semconv.GEN_AI_RESPONSE_MODEL):
        return semconv.SPAN_TYPE_LLM
    if attrs.get(semconv.LAPLACE_RETRIEVAL_QUERY):
        return semconv.SPAN_TYPE_RETRIEVAL
    return semconv.SPAN_TYPE_CHAIN


def dedup_hash(span_type: str, name: str, model: str | None, payload: Any) -> str:
    """Hash estable de (tipo, nombre, modelo, entrada).

    Dos spans con el mismo valor dentro de una traza son la misma llamada repetida:
    es la señal que alimenta la detección de bucles (Fase 2) y el diagnóstico (Fase 3).
    """
    if isinstance(payload, (dict, list)):
        normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    else:
        normalized = "" if payload is None else str(payload)
    material = "|".join([span_type, name, model or "", normalized.strip()])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


_SOLO_DIGITOS = re.compile(r"\d+")
_ESPACIOS = re.compile(r"\s+")


def loop_hash(span_type: str, name: str, payload: Any, *, ignorar_numeros: bool = True) -> str:
    """Hash de la llamada **ignorando los números**, para reconocer bucles.

    `dedup_hash` sólo pilla repeticiones exactas. Un bucle de verdad casi nunca repite
    exactamente: lleva un contador de intentos, un número de página, una marca de
    tiempo. `estado_almacen(pedido, intento=1..6)` son seis llamadas distintas para
    `dedup_hash` y la misma pregunta para cualquiera que las mire (D-109).

    Se normaliza a lo bruto —minúsculas, cada tirada de dígitos a `#`, espacios
    colapsados— porque lo que hace falta es agrupar, no medir parecido. Dos llamadas
    que sólo difieren en un número caen juntas; dos que difieren en una palabra, no.
    """
    if isinstance(payload, (dict, list)):
        texto = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    else:
        texto = "" if payload is None else str(payload)
    # En la ENTRADA los números son ruido: el contador de intentos no cambia la
    # pregunta. En la SALIDA son justo lo contrario, son el avance: «pedido 3
    # procesado» y «pedido 4 procesado» son dos resultados distintos, y borrarles el
    # número convertiría un bucle que trabaja en un bucle atascado. Lo destapó el
    # contraejemplo de `test_un_bucle_que_avanza_no_se_señala` (D-109).
    if ignorar_numeros:
        texto = _SOLO_DIGITOS.sub("#", texto)
    texto = _ESPACIOS.sub(" ", texto.lower()).strip()
    material = "|".join([span_type, name, texto])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


#: Cuánto de las instrucciones se guarda como pista legible para la interfaz.
STEP_HINT_CHARS = 80


#: Los roles que llevan las instrucciones fijas. `developer` es el nombre que les da
#: OpenAI desde los modelos de razonamiento; sin él, todo el tráfico que lo use caería
#: en un único paso por muy distintas que fueran sus instrucciones.
ROLES_DE_INSTRUCCIONES = frozenset({"system", "developer"})


def _system_text(messages: list[dict[str, Any]]) -> str:
    """El texto de los mensajes de sistema, que es la parte fija de un prompt."""
    partes: list[str] = []
    for message in messages:
        if str(message.get("role", "")).lower() not in ROLES_DE_INSTRUCCIONES:
            continue
        content = message.get("content")
        if content is None and isinstance(message.get("parts"), list):
            # Las convenciones GenAI nuevas llevan el texto en `parts`, no en `content`.
            content = _texto_de_parts(message["parts"])
        if isinstance(content, str):
            partes.append(content)
        elif content is not None:
            partes.append(json.dumps(content, sort_keys=True, ensure_ascii=False, default=str))
    return "\n".join(partes)


def _texto_de_parts(parts: list[Any]) -> Any:
    """El texto de un mensaje en `parts`, si todo son partes de texto; si no, tal cual."""
    if parts and all(isinstance(p, dict) and p.get("type") == "text" for p in parts):
        return "".join(str(p.get("content", "")) for p in parts)
    return parts


def _tool_names(raw: Any) -> list[str]:
    """Nombres de las herramientas declaradas, en los dos formatos de la industria."""
    tools = _json_or(raw, None)
    if not isinstance(tools, list):
        return []
    nombres = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        nombre = tool.get("name") or (tool.get("function") or {}).get("name")
        if nombre:
            nombres.append(str(nombre))
    return sorted(set(nombres))


def _sample_rate(valor: Any) -> float:
    """A cuántas trazas representa esta (D-181). Algo que no sea un número ≥ 1 es 1: un
    0,5 diría que la traza vale media, y eso no lo manda ningún SDK."""
    try:
        rate = float(valor)
    except (TypeError, ValueError):
        return 1.0
    return rate if 1.0 <= rate <= 1_000_000 else 1.0


def prefix_hash(messages: list[dict[str, Any]], tools: Any) -> str:
    """La huella de las instrucciones y las herramientas: lo que va delante de cada
    llamada y la caché del proveedor puede reutilizar. Es la mitad «con las mismas
    instrucciones» de la identidad de paso, y sola, sin el sitio, dice qué pasos
    distintos mandan el mismo prefijo (D-178)."""
    instrucciones = _system_text(messages)
    herramientas = _tool_names(tools)
    if not instrucciones and not herramientas:
        return ""
    material = instrucciones + "\x00" + "\x00".join(herramientas)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def step_identity(
    attrs: dict[str, Any], name: str, messages: list[dict[str, Any]], tools: Any
) -> tuple[str, str, str, str]:
    """Identidad estable de un paso: `(clave, etiqueta, pista)`.

    Un paso es **una llamada al modelo hecha desde el mismo sitio y con las mismas
    instrucciones**. Las dos mitades hacen falta:

    - *Desde el mismo sitio* es el span que la envuelve (la función decorada con
      `@observe`). Sin esto, el nombre del span de LLM es `chat <modelo>` para todas
      las llamadas del agente y las tres reglas mezclan pasos que no tienen nada que
      ver: basta una llamada corta para hundir el suelo de tokens de la regla del
      contexto fijo y para que la del modelo caro compare medias sin sentido.
    - *Con las mismas instrucciones* es la huella del prompt de sistema y de las
      herramientas declaradas. Hace falta porque quien acaba de instalar Laplace no
      decora nada: sus llamadas cuelgan todas del mismo span raíz, o de ninguno.

    Si no hay ni lo uno ni lo otro —payloads desactivados y sin decorar— se cae al
    nombre del span, que es lo que había antes. No se inventa una identidad que no se
    puede sostener (D-060).
    """
    # El sitio es el CAMINO de pasos, no sólo el padre: `atender_ticket >
    # resumir_para_crm`. Con el padre a secas, dos agentes que llamen igual a una
    # función caían en el mismo sitio y sus poblaciones se mezclaban (D-106). Se cae al
    # padre para las trazas que llegaron antes de que el SDK mandara el camino.
    sitio = str(
        attrs.get(semconv.LAPLACE_STEP_SITE) or attrs.get(semconv.LAPLACE_STEP_PARENT) or ""
    ).strip()
    padre = str(attrs.get(semconv.LAPLACE_STEP_PARENT) or "").strip()
    instrucciones = _system_text(messages)
    huella = prefix_hash(messages, tools)

    if not sitio and not huella:
        # Sin ninguna de las dos señales, el nombre es todo lo que hay.
        return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16], name, "", ""

    clave = hashlib.sha256(f"{sitio}\x00{huella}".encode()).hexdigest()[:16]
    pista = " ".join(instrucciones.split())[:STEP_HINT_CHARS]
    # La etiqueta sigue siendo el nombre a secas: es lo que se lee en pantalla, y
    # «atender_ticket > resumir_para_crm» en un titular sería ruido. El camino va
    # aparte, para agrupar.
    etiqueta = padre or sitio or pista or name
    return clave, etiqueta, pista, sitio


# ---------------------------------------------------------------------------------
# OTLP -> Span
# ---------------------------------------------------------------------------------


def parse_spans(request: ExportTraceServiceRequest) -> list[Span]:
    """Aplana la petición OTLP en spans del contrato, ya con coste calculado."""
    prices = get_price_table()
    spans: list[Span] = []
    padres, entradas = _entradas_de_los_padres(request)

    for resource_spans in request.resource_spans:
        resource = _attributes(resource_spans.resource.attributes)
        project_id = str(
            resource.get(semconv.LAPLACE_PROJECT_ID)
            or resource.get(semconv.SERVICE_NAME)
            or "default"
        )
        for scope_spans in resource_spans.scope_spans:
            for proto_span in scope_spans.spans:
                try:
                    spans.append(
                        _build_span(
                            proto_span,
                            project_id,
                            resource,
                            prices,
                            _entrada_heredada(proto_span, padres, entradas),
                        )
                    )
                except Exception:  # noqa: BLE001 - un span roto no invalida el lote
                    logger.exception("no se pudo procesar un span; se descarta")
    return spans


def _entradas_de_los_padres(request: Any) -> tuple[dict[bytes, bytes], dict[bytes, Any]]:
    """Del lote: el padre de cada span, y los mensajes que dejó Mastra en sus pasos.

    La llamada al modelo de Mastra (`model_inference`) no lleva sus mensajes: los lleva el
    paso que la envuelve (D-170). Sólo se miran las claves, que es barato; el valor sólo
    se lee en los spans que lo tienen.
    """
    padres: dict[bytes, bytes] = {}
    entradas: dict[bytes, Any] = {}
    for resource_spans in request.resource_spans:
        for scope_spans in resource_spans.scope_spans:
            for proto_span in scope_spans.spans:
                padres[proto_span.span_id] = proto_span.parent_span_id
                for par in proto_span.attributes:
                    if par.key in ENTRADAS_DEL_PADRE:
                        entradas.setdefault(proto_span.span_id, _any_value(par.value))
    return padres, entradas


def _entrada_heredada(
    proto_span: Any, padres: dict[bytes, bytes], entradas: dict[bytes, Any]
) -> str | None:
    """Los mensajes del paso de Mastra que envuelve esta llamada, si están en el lote."""
    if not entradas:
        return None
    if not any(
        par.key == "mastra.span.type" and _any_value(par.value) == "model_inference"
        for par in proto_span.attributes
    ):
        return None
    actual = proto_span.parent_span_id
    for _ in range(3):  # el paso, y por encima la generación
        if not actual:
            return None
        if actual in entradas:
            return mensajes_del_padre(entradas[actual])
        actual = padres.get(actual, b"")
    return None


def _build_span(
    proto_span: Any,
    project_id: str,
    resource: dict[str, Any],
    prices: Any,
    entrada_heredada: str | None = None,
) -> Span:
    # Lo que venga de OpenInference u OpenLLMetry se traduce aquí, antes de clasificar:
    # a partir de esta línea el span habla nuestro idioma (D-136).
    crudos = _attributes(proto_span.attributes)
    if entrada_heredada and not crudos.get(semconv.GEN_AI_INPUT_MESSAGES):
        crudos[semconv.GEN_AI_INPUT_MESSAGES] = entrada_heredada
    attrs = normalizar(crudos)
    span_type = classify(attrs)
    name = proto_span.name

    start = _ts(proto_span.start_time_unix_nano)
    end = _ts(proto_span.end_time_unix_nano)
    duration_ms = (proto_span.end_time_unix_nano - proto_span.start_time_unix_nano) / 1_000_000.0

    events = [
        SpanEvent(
            name=event.name,
            timestamp=_ts(event.time_unix_nano),
            attributes=_attributes(event.attributes),
        )
        for event in proto_span.events
    ]

    status = _STATUS_BY_CODE.get(int(proto_span.status.code), "unset")
    status_message = proto_span.status.message or ""
    if status != "error" and any(e.name == semconv.EVENT_EXCEPTION for e in events):
        # Una excepción registrada es un fallo aunque el instrumentador olvidara el estado.
        status = "error"

    llm = _build_llm(attrs, prices) if span_type == semconv.SPAN_TYPE_LLM else None
    tool = _build_tool(attrs) if span_type == semconv.SPAN_TYPE_TOOL else None
    retrieval = _build_retrieval(attrs) if span_type == semconv.SPAN_TYPE_RETRIEVAL else None

    generic_input = _json_or(attrs.get(semconv.LAPLACE_INPUT), None)
    generic_output = _json_or(attrs.get(semconv.LAPLACE_OUTPUT), None)

    if llm is not None:
        dedup_payload: Any = llm.input_messages
        salida_payload: Any = llm.output_messages
        dedup_model = llm.request_model
    elif tool is not None:
        dedup_payload = tool.arguments
        salida_payload = tool.output
        dedup_model = None
    else:
        dedup_payload = generic_input
        salida_payload = generic_output
        dedup_model = None

    if llm is not None:
        step_key, step_label, step_hint, step_site = step_identity(
            attrs, name, llm.input_messages, attrs.get("laplace.request.tools")
        )
        prefijo = prefix_hash(llm.input_messages, attrs.get("laplace.request.tools"))
    else:
        step_key = step_label = step_hint = step_site = prefijo = ""

    consumed = {
        semconv.LAPLACE_SPAN_TYPE,
        semconv.LAPLACE_STEP_PARENT,
        semconv.LAPLACE_SESSION_ID,
        semconv.LAPLACE_USER_ID,
        semconv.LAPLACE_CUSTOMER_ID,
        semconv.LAPLACE_TAGS,
        semconv.LAPLACE_METADATA,
        semconv.LAPLACE_INPUT,
        semconv.LAPLACE_OUTPUT,
        semconv.GEN_AI_INPUT_MESSAGES,
        semconv.GEN_AI_OUTPUT_MESSAGES,
        semconv.LAPLACE_TOOL_ARGUMENTS,
        semconv.LAPLACE_TOOL_OUTPUT,
        semconv.LAPLACE_RETRIEVAL_DOCUMENTS,
        semconv.LAPLACE_BILLING_TIER,
        semconv.LAPLACE_BILLING_REGION,
        semconv.LAPLACE_PROMPT_NAME,
        semconv.LAPLACE_PROMPT_VERSION,
    }

    return Span(
        span_id=proto_span.span_id.hex(),
        trace_id=proto_span.trace_id.hex(),
        parent_span_id=proto_span.parent_span_id.hex() or None,
        project_id=project_id,
        name=name,
        type=span_type,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        status_message=status_message,
        start_time=start,
        end_time=end,
        duration_ms=duration_ms,
        llm=llm,
        tool=tool,
        retrieval=retrieval,
        input=generic_input,
        output=generic_output,
        session_id=_str_or_none(attrs.get(semconv.LAPLACE_SESSION_ID)),
        user_id=_str_or_none(attrs.get(semconv.LAPLACE_USER_ID)),
        customer_id=_str_or_none(attrs.get(semconv.LAPLACE_CUSTOMER_ID)),
        tags=[str(t) for t in (_json_or(attrs.get(semconv.LAPLACE_TAGS), []) or [])],
        metadata=_json_or(attrs.get(semconv.LAPLACE_METADATA), {}) or {},
        dedup_hash=dedup_hash(span_type, name, dedup_model, dedup_payload),
        loop_hash=loop_hash(span_type, name, dedup_payload),
        # El de la salida sirve para la otra mitad de la pregunta: un bucle que da
        # vueltas sin avanzar produce siempre lo mismo (D-109).
        loop_out_hash=loop_hash(span_type, name, salida_payload, ignorar_numeros=False),
        step_key=step_key,
        step_site=step_site,
        step_label=step_label,
        step_hint=step_hint,
        prefix_hash=prefijo,
        sample_rate=_sample_rate(attrs.get("laplace.sample.rate")),
        # El SDK sólo escribe esto cuando ha comprobado que el texto de esa versión iba
        # de verdad en los mensajes. Aquí se copia tal cual: la ingesta no deduce una
        # versión que el emisor no haya afirmado (D-090).
        prompt_name=str(attrs.get(semconv.LAPLACE_PROMPT_NAME) or ""),
        prompt_version=_entero(attrs.get(semconv.LAPLACE_PROMPT_VERSION)),
        events=events,
        attributes={
            k: v
            for k, v in attrs.items()
            if k not in consumed and not k.startswith(TRADUCIDOS)
        },
    )


def _coste(prices: Any, model: str | None, usage: TokenUsage, tier: str, region: str) -> Cost:
    """El coste de una llamada. Lo usan la ingesta y el recálculo, y es el mismo."""
    breakdown = prices.compute(
        model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cache_write_1h_tokens=usage.cache_write_1h_tokens,
        tier=tier,
        region=region,
    )
    return Cost(
        input_usd=breakdown.input_usd,
        output_usd=breakdown.output_usd,
        total_usd=breakdown.total_usd,
        cache_read_usd=breakdown.cache_read_usd,
        cache_write_usd=breakdown.cache_write_usd,
        cache_saving_usd=breakdown.cache_saving_usd,
        unknown=breakdown.unknown,
        rate_assumed=breakdown.assumed,
        rate_unverified=breakdown.unverified,
        rate_note=breakdown.note,
        rate=breakdown.rate,
    )


def recalcular_coste(span: Span, prices: Any) -> Span:
    """El mismo span con el coste calculado otra vez contra la tabla en vigor.

    Existe para las tarifas propias (D-123): el coste se calcula en la ingesta, así que
    un precio añadido después no alcanzaba a lo ya guardado y el modelo seguía saliendo
    como «no lo sabemos» en todo el histórico.
    """
    if span.llm is None:
        return span
    llm = span.llm
    coste = _coste(
        prices,
        llm.response_model or llm.request_model,
        llm.usage,
        llm.billing_tier,
        llm.billing_region,
    )
    return span.model_copy(update={"llm": llm.model_copy(update={"cost": coste})})


def _build_llm(attrs: dict[str, Any], prices: Any) -> LLMAttributes:
    usage = TokenUsage(
        input_tokens=int(attrs.get(semconv.GEN_AI_USAGE_INPUT_TOKENS) or 0),
        output_tokens=int(attrs.get(semconv.GEN_AI_USAGE_OUTPUT_TOKENS) or 0),
        cached_input_tokens=int(attrs.get(semconv.LAPLACE_USAGE_CACHED_INPUT_TOKENS) or 0),
        cache_write_tokens=int(attrs.get(semconv.LAPLACE_USAGE_CACHE_WRITE_TOKENS) or 0),
        cache_write_1h_tokens=int(attrs.get(semconv.LAPLACE_USAGE_CACHE_WRITE_1H_TOKENS) or 0),
        reasoning_tokens=int(attrs.get(semconv.LAPLACE_USAGE_REASONING_TOKENS) or 0),
        estimated=bool(attrs.get(semconv.LAPLACE_USAGE_ESTIMATED) or False),
    )
    request_model = _str_or_none(attrs.get(semconv.GEN_AI_REQUEST_MODEL))
    response_model = _str_or_none(attrs.get(semconv.GEN_AI_RESPONSE_MODEL))
    entrada = _as_message_list(attrs.get(semconv.GEN_AI_INPUT_MESSAGES))
    salida = _as_message_list(attrs.get(semconv.GEN_AI_OUTPUT_MESSAGES))
    if (
        (request_model or response_model)
        and semconv.GEN_AI_USAGE_INPUT_TOKENS not in attrs
        and semconv.GEN_AI_USAGE_OUTPUT_TOKENS not in attrs
        # Sólo si hubo respuesta: una llamada que no llegó al proveedor (un error de
        # conexión) no se cobró, y estimarle la entrada sería inventarse una factura.
        and salida
    ):
        # Una llamada con respuesta y sin recuento costaba 0 $ medidos. Pasa, por
        # ejemplo, con Mastra por la API de Chat en streaming, que no pide el uso al
        # proveedor. Se cuenta como lo cuenta el SDK en el mismo caso —por el texto— y se
        # marca como estimado, que la interfaz ya distingue (D-183).
        usage = usage.model_copy(update={
            "input_tokens": estimate_messages_tokens(entrada) if entrada else 0,
            "output_tokens": estimate_messages_tokens(salida),
            "estimated": True,
        })
    # Sin atributo, el metro es el estándar: es lo que los proveedores facturan por
    # defecto, no una suposición que haya que marcar como tal.
    tier = str(attrs.get(semconv.LAPLACE_BILLING_TIER) or "standard")
    region = str(attrs.get(semconv.LAPLACE_BILLING_REGION) or "global")

    coste = _coste(prices, response_model or request_model, usage, tier, region)

    params = {
        key.rsplit(".", 1)[-1]: value
        for key, value in attrs.items()
        if key.startswith("gen_ai.request.") and key != semconv.GEN_AI_REQUEST_MODEL
    }

    finish = attrs.get(semconv.GEN_AI_RESPONSE_FINISH_REASONS) or []
    if isinstance(finish, str):
        finish = [finish]

    return LLMAttributes(
        # `gen_ai.provider.name` es el nombre que le dan las convenciones GenAI nuevas,
        # y el que ya manda OpenLLMetry-js.
        system=_str_or_none(attrs.get(semconv.GEN_AI_SYSTEM) or attrs.get("gen_ai.provider.name")),
        request_model=request_model,
        response_model=response_model,
        response_id=_str_or_none(attrs.get(semconv.GEN_AI_RESPONSE_ID)),
        operation=_str_or_none(attrs.get(semconv.GEN_AI_OPERATION_NAME)),
        billing_tier=tier,
        billing_region=region,
        usage=usage,
        cost=coste,
        input_messages=entrada,
        output_messages=salida,
        params=params,
        finish_reasons=[str(r) for r in finish],
    )


def _build_tool(attrs: dict[str, Any]) -> ToolAttributes:
    return ToolAttributes(
        name=_str_or_none(attrs.get(semconv.GEN_AI_TOOL_NAME)),
        call_id=_str_or_none(attrs.get(semconv.GEN_AI_TOOL_CALL_ID)),
        description=_str_or_none(attrs.get(semconv.GEN_AI_TOOL_DESCRIPTION)),
        arguments=_json_or(attrs.get(semconv.LAPLACE_TOOL_ARGUMENTS), None),
        output=_json_or(attrs.get(semconv.LAPLACE_TOOL_OUTPUT), None),
    )


def _build_retrieval(attrs: dict[str, Any]) -> RetrievalAttributes:
    documents = _json_or(attrs.get(semconv.LAPLACE_RETRIEVAL_DOCUMENTS), []) or []
    if not isinstance(documents, list):
        documents = [{"content": documents}]
    return RetrievalAttributes(
        query=_str_or_none(attrs.get(semconv.LAPLACE_RETRIEVAL_QUERY)),
        top_k=int(attrs[semconv.LAPLACE_RETRIEVAL_TOP_K])
        if attrs.get(semconv.LAPLACE_RETRIEVAL_TOP_K) is not None
        else None,
        documents=[d if isinstance(d, dict) else {"content": d} for d in documents],
    )


def _entero(value: Any) -> int:
    """Un entero, o cero. Una versión ilegible no puede tumbar la ingesta de un span."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _str_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
