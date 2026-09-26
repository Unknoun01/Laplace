"""Nombres de atributos de span.

Módulo deliberadamente sin dependencias: sólo constantes de string. Es lo único que
importa el camino caliente del SDK (emitir spans), de modo que instrumentar un agente
no arrastra Pydantic ni nada más.

Se siguen las convenciones semánticas GenAI de OpenTelemetry. Todo lo que el estándar
no cubre vive bajo `laplace.*`; nunca se inventan nombres dentro de `gen_ai.*`.
"""

from __future__ import annotations

# --- Tipos de span (ver docs/trace-contract.md §1) ---------------------------------

SPAN_TYPE_AGENT = "agent"
SPAN_TYPE_LLM = "llm"
SPAN_TYPE_TOOL = "tool"
SPAN_TYPE_RETRIEVAL = "retrieval"
SPAN_TYPE_CHAIN = "chain"

SPAN_TYPES = (
    SPAN_TYPE_AGENT,
    SPAN_TYPE_LLM,
    SPAN_TYPE_TOOL,
    SPAN_TYPE_RETRIEVAL,
    SPAN_TYPE_CHAIN,
)

# --- Operaciones GenAI --------------------------------------------------------------

OPERATION_CHAT = "chat"
OPERATION_TEXT_COMPLETION = "text_completion"
OPERATION_EMBEDDINGS = "embeddings"
OPERATION_EXECUTE_TOOL = "execute_tool"
OPERATION_INVOKE_AGENT = "invoke_agent"

# --- Atributos estándar de OpenTelemetry GenAI --------------------------------------

GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_SYSTEM = "gen_ai.system"

GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_TOP_P = "gen_ai.request.top_p"
GEN_AI_REQUEST_TOP_K = "gen_ai.request.top_k"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_REQUEST_STOP_SEQUENCES = "gen_ai.request.stop_sequences"
GEN_AI_REQUEST_FREQUENCY_PENALTY = "gen_ai.request.frequency_penalty"
GEN_AI_REQUEST_PRESENCE_PENALTY = "gen_ai.request.presence_penalty"
GEN_AI_REQUEST_SEED = "gen_ai.request.seed"

GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_ID = "gen_ai.response.id"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"

GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

# Mensajes serializados como JSON (D-003).
GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"

GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
GEN_AI_TOOL_DESCRIPTION = "gen_ai.tool.description"

GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_AGENT_ID = "gen_ai.agent.id"

# --- Extensiones de Laplace ---------------------------------------------------------

LAPLACE_SPAN_TYPE = "laplace.span.type"
LAPLACE_SESSION_ID = "laplace.session.id"
LAPLACE_USER_ID = "laplace.user.id"
LAPLACE_TAGS = "laplace.tags"
LAPLACE_METADATA = "laplace.metadata"

#: Tokens de entrada servidos desde caché. Subconjunto de `gen_ai.usage.input_tokens`,
#: que en el contrato es el total facturable de entrada (D-050).
LAPLACE_USAGE_CACHED_INPUT_TOKENS = "laplace.usage.cached_input_tokens"
#: Tokens escritos en caché. Se cobran por encima de la tarifa de entrada (1,25x en
#: caché corta, 2x en la de una hora), así que no contarlos aparte falsea la factura.
LAPLACE_USAGE_CACHE_WRITE_TOKENS = "laplace.usage.cache_write_tokens"
LAPLACE_USAGE_CACHE_WRITE_1H_TOKENS = "laplace.usage.cache_write_1h_tokens"
LAPLACE_USAGE_REASONING_TOKENS = "laplace.usage.reasoning_tokens"
#: True cuando los tokens los hemos contado nosotros porque el proveedor no los dio.
#: La interfaz distingue medido de estimado: no es lo mismo un coste que sale de la
#: factura que uno que sale de dividir caracteres entre cuatro.
LAPLACE_USAGE_ESTIMATED = "laplace.usage.estimated"

LAPLACE_STREAMING = "laplace.streaming"

#: Nombre del paso que envuelve a esta llamada al modelo: la función decorada con
#: `@observe` o el span manual dentro del que se hizo. Es lo que identifica «desde
#: dónde» se llama, porque el nombre del propio span de LLM es `chat <modelo>` y ese
#: es el mismo para todas las llamadas del agente (D-060).
LAPLACE_STEP_PARENT = "laplace.step.parent"
#: El camino entero de pasos hasta la llamada («atender_ticket > resumir_para_crm»).
#: El nombre del padre a secas mezcla dos agentes que llamen igual a una función,
#: y con ellos sus poblaciones de llamadas (D-106).
LAPLACE_STEP_SITE = "laplace.step.site"

#: Metro de facturación de la llamada: `standard`, `batch` o `fast`. Sólo se pone
#: cuando la petición lo dice; su ausencia significa estándar, que es el defecto de
#: los dos proveedores, no una suposición nuestra.
LAPLACE_BILLING_TIER = "laplace.billing.tier"
#: `regional` cuando la petición pide residencia de datos, que lleva recargo.
LAPLACE_BILLING_REGION = "laplace.billing.region"

#: Prompt gestionado con el que se hizo esta llamada, y su versión. Los pone el SDK
#: cuando el prompt se ha pedido con `laplace.get_prompt(...)` **y su texto aparece de
#: verdad en los mensajes enviados**. Nunca se deducen: si el texto no está, no se
#: escribe nada, porque una versión mal atribuida contamina las métricas de todas.
LAPLACE_PROMPT_NAME = "laplace.prompt.name"
#: Número de versión. `0` significa que se sirvió el texto de reserva porque Laplace no
#: respondió: es tráfico real que no corresponde a ninguna versión guardada, y eso se
#: dice en vez de atribuírselo a la que estuviera en producción.
LAPLACE_PROMPT_VERSION = "laplace.prompt.version"

LAPLACE_TOOL_ARGUMENTS = "laplace.tool.arguments"
LAPLACE_TOOL_OUTPUT = "laplace.tool.output"

LAPLACE_RETRIEVAL_QUERY = "laplace.retrieval.query"
LAPLACE_RETRIEVAL_DOCUMENTS = "laplace.retrieval.documents"
LAPLACE_RETRIEVAL_TOP_K = "laplace.retrieval.top_k"

LAPLACE_INPUT = "laplace.input"
LAPLACE_OUTPUT = "laplace.output"

# --- Atributos de recurso -----------------------------------------------------------

LAPLACE_PROJECT_ID = "laplace.project.id"
SERVICE_NAME = "service.name"
SERVICE_VERSION = "service.version"

# --- Eventos de span ----------------------------------------------------------------

EVENT_EXCEPTION = "exception"
EXCEPTION_TYPE = "exception.type"
EXCEPTION_MESSAGE = "exception.message"
EXCEPTION_STACKTRACE = "exception.stacktrace"

# --- Sistemas GenAI conocidos --------------------------------------------------------

SYSTEM_OPENAI = "openai"
SYSTEM_ANTHROPIC = "anthropic"
SYSTEM_AZURE_OPENAI = "az.ai.openai"
SYSTEM_GOOGLE = "gcp.gemini"
SYSTEM_MISTRAL = "mistral_ai"
SYSTEM_COHERE = "cohere"


#: Etiqueta de la raíz de cada ejecución lanzada por `run_dataset`. El backend la usa
#: para que un experimento hecho a propósito no salga como algo que arreglar (D-135).
EVAL_TAG = "laplace-eval"
