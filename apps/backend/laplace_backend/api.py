"""API HTTP: ingesta OTLP y lectura de trazas."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from laplace.schema import Trace, TraceListPage
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
from starlette.concurrency import run_in_threadpool

from . import grafo, idioma
from .auth import ALL_PROJECTS, identity_of
from .cache_diagnostico import CacheDiagnostico
from .config import get_settings
from .ingest.otlp import OTLP_EXPANSION, CuerpoDemasiadoGrande, decode_request, parse_spans
from .insights import FindingDetail, Overview, overview
from .insights import detail as finding_detail
from .panel import Panel
from .panel import build as build_panel
from .pricing import get_price_table, reload_price_table
from .seguimiento import aplicar_estados, leer_estados
from .storage.base import TraceFilter, Window, decode_cursor
from .textos import t
from .tree import build_tree, summarize

logger = logging.getLogger("laplace.api")

router = APIRouter()

_PROTOBUF = "application/x-protobuf"


def _store(request: Request) -> Any:
    return request.app.state.store


def _metadata(request: Request) -> Any:
    return request.app.state.metadata


# ---------------------------------------------------------------------------------
# Ingesta — ruta OTLP estándar
# ---------------------------------------------------------------------------------


@router.post("/v1/traces", include_in_schema=True)
async def ingest_traces(request: Request) -> Response:
    """Endpoint OTLP/HTTP.

    Es la ruta estándar, así que cualquier proceso instrumentado con OpenTelemetry
    puede exportar aquí apuntando `OTEL_EXPORTER_OTLP_ENDPOINT` a este backend,
    con o sin el SDK de Laplace.
    """
    body = await request.body()
    tope = request.app.state.settings.max_body_bytes * OTLP_EXPANSION
    try:
        decoded = decode_request(
            body,
            content_type=request.headers.get("content-type", ""),
            content_encoding=request.headers.get("content-encoding", ""),
            max_bytes=tope,
        )
    except CuerpoDemasiadoGrande as exc:
        # 413 y no 400: el exportador sabe que no tiene que reintentar el mismo lote.
        logger.warning("petición OTLP demasiado grande: %s", exc)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("petición OTLP ilegible: %s", exc)
        raise HTTPException(status_code=400, detail=t("error.otlp_ilegible")) from exc

    # Fuera del bucle de eventos: traducir un lote grande son decenas de milisegundos de
    # CPU, y mientras tanto no avanzaba ninguna otra petición (D-142).
    spans = await run_in_threadpool(parse_spans, decoded)

    # La clave ata el proyecto. Los spans traen el suyo dentro del protobuf —lo pone
    # `laplace.init(project=...)`— así que aquí es donde se comprueba que coincide. Se
    # rechaza el lote entero en vez de reetiquetarlo: reetiquetar escondería una
    # configuración mal puesta y el usuario descubriría dentro de un mes que su tráfico
    # lleva semanas en el proyecto de otro (D-097).
    identidad = identity_of(request)
    ajenos = sorted({s.project_id for s in spans if not identidad.allows(s.project_id)})
    if ajenos:
        raise HTTPException(
            status_code=403,
            detail=t("error.otlp_proyecto_ajeno", proyectos=", ".join(ajenos)),
        )

    if spans:
        store = _store(request)
        try:
            await run_in_threadpool(store.insert_spans, spans)
        except Exception as exc:  # noqa: BLE001
            # 503 y no 500: el exportador reintenta, y el span es idempotente al escribir.
            logger.exception("fallo al escribir spans")
            raise HTTPException(status_code=503, detail=t("error.almacenamiento")) from exc

        projects = {span.project_id for span in spans}
        for project_id in projects:
            try:
                await run_in_threadpool(_metadata(request).ensure_project, project_id)
            except Exception:  # noqa: BLE001 - registrar el proyecto no es crítico
                logger.debug("no se pudo registrar el proyecto %s", project_id, exc_info=True)

    logger.debug("ingeridos %d spans", len(spans))
    return Response(
        content=ExportTraceServiceResponse().SerializeToString(),
        media_type=_PROTOBUF,
    )


# ---------------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------------


#: La ventana de la lista de trazas cuando la petición no trae `since`.
VENTANA_LISTA_DIAS = 30


@router.get("/api/traces", response_model=TraceListPage)
async def list_traces(
    request: Request,
    project_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    #: Valor de `next_cursor` de la página anterior. Opaco: no lo construyas a mano.
    cursor: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    status: str | None = Query(None, pattern="^(ok|error)$"),
    session_id: str | None = None,
    user_id: str | None = None,
    #: El cliente que paga (D-161): de la pestaña de Clientes a sus ejecuciones.
    customer_id: str | None = None,
    search: str | None = None,
    #: Texto a buscar dentro de prompts, respuestas y herramientas. Con menos de tres
    #: caracteres casa casi todo y ningún índice ayuda (D-144).
    content: str | None = Query(None, min_length=3, max_length=200),
    #: Identidad exacta de un paso: la que trae cada hallazgo en `step_key`.
    step_key: str | None = None,
    span_type: str | None = None,
    sort: str = Query("recent", pattern="^(recent|cost|duration)$"),
    #: Filtros que sólo ofrece el modo avanzado del explorador.
    model: str | None = None,
    min_cost_usd: float | None = Query(None, ge=0),
) -> TraceListPage:
    """Lista de trazas del proyecto y rango activos."""
    # Sin proyecto en la petición, una clave de un proyecto vería la lista entera de la
    # instalación. El middleware comprueba el `project_id` que venga; el que no venga lo
    # pone aquí la identidad.
    project_id = identity_of(request).scope(project_id)
    before, before_trace_id = decode_cursor(cursor)
    # Sin ventana, la lista recorría todo el histórico del proyecto (D-008b). La interfaz
    # siempre la pasa; un cliente de la API no tiene por qué saberlo. Por defecto, los
    # últimos días de `VENTANA_LISTA_DIAS`; quien quiera más atrás lo pide con `since`.
    if since is None:
        since = (until or datetime.now(timezone.utc)) - timedelta(days=VENTANA_LISTA_DIAS)
    filters = TraceFilter(
        project_id=project_id,
        limit=limit,
        before=before,
        before_trace_id=before_trace_id,
        since=since,
        until=until,
        status=status,
        session_id=session_id,
        user_id=user_id,
        customer_id=customer_id,
        search=search,
        content=content,
        step_key=step_key,
        span_type=span_type,
        sort=sort,
        model=model,
        min_cost_usd=min_cost_usd,
    )
    store = _store(request)
    page = await run_in_threadpool(store.list_traces, filters)

    # Marca de bucle: una segunda consulta sobre las trazas ya seleccionadas.
    ids = [t.trace_id for t in page.traces]
    with_repeats = await run_in_threadpool(store.traces_with_repeats, project_id, ids)

    return TraceListPage(
        traces=page.traces,
        next_cursor=page.next_cursor,
        with_repeats=sorted(with_repeats),
    )


def _trace_por_prefijo(request: Request, prefijo: str, project_id: str) -> str | None:
    """El id completo de la única traza del proyecto que empieza por `prefijo`.

    `None` si no es un prefijo hexadecimal de al menos 8 caracteres, si no hay ninguna o
    si hay más de una: con dos candidatas, abrir una cualquiera sería adivinar.
    """
    prefijo = prefijo.lower()
    if not 8 <= len(prefijo) < 32 or any(c not in "0123456789abcdef" for c in prefijo):
        return None
    pagina = _store(request).list_traces(
        TraceFilter(project_id=project_id, search=prefijo, limit=50)
    )
    ids = {t.trace_id for t in pagina.traces if t.trace_id.startswith(prefijo)}
    return ids.pop() if len(ids) == 1 else None


@router.get("/api/traces/{trace_id}", response_model=Trace)
async def get_trace(
    request: Request, trace_id: str, project_id: str | None = None
) -> Trace:
    """Árbol completo de una traza, con coste por nodo y por subárbol.

    El `project_id` es opcional en la URL, así que aquí es donde una clave de un
    proyecto podría acabar leyendo la traza de otro: sin acotar, la consulta busca ese
    id en toda la instalación. `scope()` fija el proyecto de la clave cuando la petición
    no lo dice (D-097).
    """
    alcance = identity_of(request).scope(project_id)
    spans = await run_in_threadpool(_store(request).get_trace_spans, trace_id, alcance)
    if not spans and alcance:
        # La lista enseña los 12 primeros caracteres del id, y es lo que la gente copia.
        # Sólo con proyecto: buscar un prefijo en toda la instalación sería un rastreo.
        completo = await run_in_threadpool(_trace_por_prefijo, request, trace_id, alcance)
        if completo:
            trace_id = completo
            spans = await run_in_threadpool(
                _store(request).get_trace_spans, trace_id, alcance
            )
    if not spans:
        raise HTTPException(status_code=404, detail=t("error.traza_no_encontrada"))

    metadata = _metadata(request)
    return Trace(
        summary=summarize(spans, trace_id, alcance or spans[0].project_id),
        roots=build_tree(spans),
        # El diagnóstico sigue siendo hueco de la Fase 3; las anotaciones ya no lo
        # son: las llena la pestaña de Evaluaciones, y vienen con su fuente puesta.
        # Acotado al proyecto, como las anotaciones (D-180).
        diagnosis=await run_in_threadpool(
            metadata.get_diagnosis, trace_id, alcance or spans[0].project_id
        ),
        # Acotadas al proyecto de la traza: un `trace_id` no es un secreto, y sin acotar
        # saldrían aquí las anotaciones que otro proyecto hubiera colgado de ese id.
        annotations=await run_in_threadpool(
            metadata.list_annotations, trace_id, alcance or spans[0].project_id
        ),
    )


# ---------------------------------------------------------------------------------
# Diagnóstico y ahorro (Fase 2)
# ---------------------------------------------------------------------------------


def _window(days: int) -> Window:
    """Ventana de análisis. Todas las pantallas comparten el mismo rango.

    El principio, en minuto entero: los preagregados son por minuto, y un principio a
    mitad de minuto obligaba a leer ese trozo en crudo en cada lector (D-177). Son menos
    de sesenta segundos más de ventana. El final sigue siendo ahora, para que lo que
    acaba de llegar se vea.
    """
    until = datetime.now(timezone.utc)
    since = (until - timedelta(days=days)).replace(second=0, microsecond=0)
    return Window(since=since, until=until, days=days)


#: El Diagnóstico recordado un minuto en la nube; lo borra cualquier cambio por la API.
CACHE_DIAGNOSTICO = CacheDiagnostico()


@router.get("/api/overview", response_model=Overview)
async def get_overview(
    request: Request,
    project_id: str,
    days: int = Query(7, ge=1, le=90),
) -> Overview:
    """Cuánto cuesta el agente, cuánto sobra y qué hay que arreglar."""
    # Si el proyecto no gestiona prompts, que no haya versiones en las trazas no es un
    # defecto suyo: es que no usa esa parte. La señal se enseña igual pero no pinta de
    # rojo ni cuenta para el veredicto, y eso hay que saberlo aquí (D-096).
    gestiona = bool(await run_in_threadpool(_metadata(request).list_prompts, project_id))
    estados = await run_in_threadpool(leer_estados, _metadata(request), project_id)
    segundos = get_settings().cache_diagnostico_s
    # El idioma va en la clave: el Diagnóstico lleva frases redactadas, y el de quien lo
    # pidió en inglés no puede servírsele a quien lo pide en español (D-147).
    lengua = idioma.actual()
    clave = (
        project_id, days, gestiona, lengua, json.dumps(estados, sort_keys=True, default=str)
    )
    guardado = CACHE_DIAGNOSTICO.leer(clave, segundos)
    if guardado is not None:
        return guardado
    store = _store(request)

    def calcular() -> Overview:
        # La ventana se calcula al llamar, no al definir: al renovarse en segundo plano
        # tiene que terminar en el ahora de ese momento (D-143). Y el idioma se fija
        # aquí: el renovador no viene de ninguna petición y no hereda el de nadie.
        with idioma.usar(lengua):
            return overview(
                store, project_id, _window(days), has_managed_prompts=gestiona, states=estados
            )

    resultado = await run_in_threadpool(calcular)
    CACHE_DIAGNOSTICO.guardar(clave, resultado, segundos, calcular)
    return resultado


@router.get("/api/findings/{finding_id:path}", response_model=FindingDetail)
async def get_finding(
    request: Request,
    finding_id: str,
    project_id: str,
    days: int = Query(7, ge=1, le=90),
) -> FindingDetail:
    """Ficha completa de un hallazgo.

    El identificador es determinista (`tipo:clave`), así que se recalcula sobre la misma
    ventana en lugar de guardarse. Si el problema ya no aparece —porque el usuario lo
    arregló— devuelve 404, que es exactamente lo que queremos decir.
    """
    found = await run_in_threadpool(
        finding_detail, _store(request), project_id, _window(days), finding_id
    )
    if found is None:
        raise HTTPException(status_code=404, detail=t("error.problema_no_aparece"))
    # El estado que le haya puesto el usuario, con su comprobación si lo marcó como
    # arreglado: la ficha es donde se lee si el arreglo ha servido (D-123).
    estados = await run_in_threadpool(leer_estados, _metadata(request), project_id)
    if finding_id in estados:
        await run_in_threadpool(
            aplicar_estados, _store(request), project_id, [found], estados
        )
    return found


@router.get("/api/projects")
async def list_projects(request: Request) -> dict[str, Any]:
    """Proyectos con datos, con su volumen y coste acumulado.

    Filtrada por la identidad: con una clave de un proyecto, esta lista es de un
    proyecto. Sin este filtro, el selector de la barra superior sería un directorio de
    los clientes de la instalación —nombres, volumen y gasto— para cualquiera con una
    clave cualquiera.

    Al final van, a cero, los proyectos que la identidad tiene nombrados y que todavía
    no han mandado nada (D-175): quien entraba con la clave de un proyecto recién creado
    leía «todavía no hay ningún proyecto» y un `init` con `project="mi-agente"`, que es
    justo el código que no tiene que copiar.
    """
    identidad = identity_of(request)
    stats = [
        s
        for s in await run_in_threadpool(_store(request).list_projects)
        if identidad.allows(s.project_id)
    ]
    con_datos = {s.project_id for s in stats}
    vacios = sorted(p for p in identidad.projects if p != ALL_PROJECTS and p not in con_datos)
    return {
        "projects": [
            {
                "id": s.project_id,
                "trace_count": s.trace_count,
                "span_count": s.span_count,
                "total_cost_usd": s.total_cost_usd,
                "last_seen": s.last_seen,
            }
            for s in stats
        ]
        + [
            {"id": p, "trace_count": 0, "span_count": 0, "total_cost_usd": 0.0,
             "last_seen": None}
            for p in vacios
        ]
    }


@router.get("/api/panel", response_model=Panel)
async def get_panel(
    request: Request,
    project_id: str,
    days: int = Query(7, ge=1, le=90),
) -> Panel:
    """El panel: coste por unidad de trabajo, la lectura en palabras y los picos.

    Todo lo que devuelve es de la ventana pedida: aquí no se proyecta nada. La
    comparación es contra el periodo inmediatamente anterior de la misma duración, y
    cuando ese periodo no tiene ejecuciones las métricas vienen a `None` con su motivo
    en lugar de a cero (D-073, D-077).
    """
    return await run_in_threadpool(build_panel, _store(request), project_id, _window(days))


@router.get("/api/graph", response_model=grafo.ProjectGraph)
async def get_graph(
    request: Request,
    project_id: str,
    days: int = Query(7, ge=1, le=90),
) -> grafo.ProjectGraph:
    """El agente entero en la ventana: pasos, modelos y coste por arista (D-188)."""
    return await run_in_threadpool(
        grafo.del_proyecto, _store(request), project_id, _window(days)
    )


@router.get("/api/alerts")
async def alerts_status(
    request: Request, project_id: str | None = None
) -> dict[str, Any]:
    """Qué alertas están configuradas y cuáles saltarían ahora mismo.

    Sólo lee: **no manda nada**. Existe porque la pregunta de quien acaba de configurar
    un webhook es «¿esto va a avisarme?», y la única respuesta honesta antes de que
    salte la primera alerta es enseñarle la decisión que se tomaría.
    """
    runner = getattr(request.app.state, "alerts", None)
    # Sin proyecto en la petición, los que esta identidad puede ver y ni uno más. Antes
    # salían todos los de la instalación: con la clave de un proyecto se leían el nombre,
    # los umbrales y los hallazgos de los demás. El `project_id` que venga ya lo ha
    # comprobado el middleware.
    if project_id:
        proyectos = [project_id]
    else:
        todos = await run_in_threadpool(_store(request).list_projects)
        proyectos = identity_of(request).visible([p.project_id for p in todos])
    if runner is None or not any(runner.config_for(pid).enabled for pid in proyectos):
        return {
            "enabled": False,
            "detail": t("estado.alertas_apagadas"),
            "projects": [],
        }

    salida = []
    for pid in proyectos:
        ajustes = runner.config_for(pid)
        decision = await run_in_threadpool(runner.evaluate, pid, dry_run=True)
        salida.append(
            {
                "project_id": pid,
                # El webhook NUNCA sale por la API: es un secreto, y esta ruta no está
                # autenticada. Sólo se dice si hay uno puesto.
                "webhook_configured": bool(ajustes.webhook_url),
                "muted": ajustes.muted,
                "muted_kinds": sorted(ajustes.muted_kinds),
                "min_usd": ajustes.min_usd,
                "quiet_hours": ajustes.quiet_hours,
                "window_days": ajustes.window_days,
                "would_alert": [f.id for f in decision.due],
                "in_quiet_period": [f.id for f in decision.silenced],
                "reason": decision.reason,
            }
        )
    return {"enabled": True, "projects": salida}


@router.get("/api/pricing/models")
async def pricing_models() -> dict[str, Any]:
    """Tabla de precios en uso. USD por millón de tokens."""
    table = get_price_table()
    return {
        "models": {
            name: {
                "input": price.input,
                "output": price.output,
                "cached_input": price.cached_input,
            }
            for name, price in sorted(table.models.items())
        }
    }


@router.post("/api/pricing/reload")
async def pricing_reload(request: Request) -> dict[str, Any]:
    """Recarga la tabla de precios sin reiniciar el proceso (D-006).

    Pide clave de **instalación**, no de proyecto. Es la primera ruta del producto que
    no lleva `project_id`, así que el middleware no tiene por dónde acotarla y hay que
    acotarla aquí a mano —el punto ciego que D-097 dejó anotado por si aparecía—. Lo que
    toca es la tabla de precios de todo el despliegue: con la clave de un proyecto se
    podía cambiar la aritmética con la que se le factura a los demás (D-121).
    """
    identidad = identity_of(request)
    if not identidad.sees_everything:
        raise HTTPException(
            status_code=403,
            detail=t("error.solo_instalacion.precios"),
        )
    table = await run_in_threadpool(reload_price_table)
    return {"models": len(table.models)}


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Qué almacenes hay y si responden. Sólo se afirma lo que se ha comprobado.

    En modo local no hay ClickHouse ni Postgres: antes se decía `clickhouse: true,
    postgres: true` porque se contestaba con la salud de SQLite bajo esos nombres. Ahora
    se nombra lo que hay, y las claves de la nube valen `null` donde no existen.
    """
    store, metadata = _store(request), _metadata(request)
    store_ok = await run_in_threadpool(store.health)
    metadata_ok = await run_in_threadpool(metadata.health)
    tipo_store = "clickhouse" if type(store).__name__ == "ClickHouseStore" else "sqlite"
    tipo_meta = {
        "PostgresMetadataStore": "postgres",
        "SQLiteMetadataStore": "sqlite",
    }.get(type(metadata).__name__, "none")
    return {
        "status": "ok" if store_ok else "degraded",
        "store": tipo_store,
        "store_ok": store_ok,
        "metadata": tipo_meta,
        "metadata_ok": metadata_ok,
        "clickhouse": store_ok if tipo_store == "clickhouse" else None,
        "postgres": metadata_ok if tipo_meta == "postgres" else None,
    }
