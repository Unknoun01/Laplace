"""Prompts: versiones, diff, rollback y —lo que importa— las métricas pegadas.

Lo que se comprueba aquí es lo que separa esta pestaña de un repositorio de texto:

1. **Que las guardas son las mismas que en Evaluaciones, no una copia.** Un acierto por
   versión con cuatro ejecuciones anotadas no se enseña como porcentaje, y dos versiones
   con márgenes solapados no tienen ganador (D-087).
2. **Que el tráfico se atribuye a la versión que lo produjo y a ninguna otra.** El texto
   de reserva no cuenta como la versión de producción, y una versión sin tráfico vale
   `None` y no cero.
3. **Que el pico se atribuye con las trazas, no con la hora del despliegue** (D-092).
4. **Que sin adoptar nada la pestaña enseña algo**, y que no enseña doscientas
   «versiones» cuando lo que hay es un prompt con datos variables dentro (D-093).
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from laplace.schema import (
    Annotation,
    Cost,
    LLMAttributes,
    Prompt,
    Span,
    TokenUsage,
)

from laplace_backend import prompts
from laplace_backend.panel import attribute
from laplace_backend.storage.base import ObservedPrompt, PromptUsage, Window, WindowFacts
from laplace_backend.storage.metadata import SQLiteMetadataStore
from laplace_backend.storage.sqlite import SQLiteStore

#: Relativo a ahora, no una fecha fija: los tests que piden la vista por la API usan
#: una ventana de días desde hoy, así que una fecha escrita a mano los pone en rojo
#: solos al cabo de una semana, por algo que no tiene que ver con lo que comprueban.
AHORA = datetime.now(timezone.utc) - timedelta(minutes=30)


# ---------------------------------------------------------------------------------
# Constructores
# ---------------------------------------------------------------------------------


def _uso(version: int, *, trazas: int, coste_por_traza: float, **extra) -> PromptUsage:
    return PromptUsage(
        name="resumen",
        version=version,
        traces=trazas,
        calls=trazas,
        cost_usd=coste_por_traza * trazas,
        input_tokens=1000 * trazas,
        output_tokens=100 * trazas,
        first_seen=AHORA,
        last_seen=AHORA + timedelta(hours=1),
        **extra,
    )


def _version(numero: int, texto: str) -> object:
    from laplace.schema import PromptVersion

    return PromptVersion(
        id=f"pv_{numero}",
        prompt_id="pr_1",
        version=numero,
        text=texto,
        author="enol",
        created_at=AHORA,
    )


def _prompt(produccion: int | None = 2) -> Prompt:
    return Prompt(
        id="pr_1",
        project_id="p",
        name="resumen",
        created_at=AHORA,
        production_version=produccion,
        version_count=2,
    )


# ---------------------------------------------------------------------------------
# Las guardas, que son las de Evaluaciones y no una copia (D-087)
# ---------------------------------------------------------------------------------


def test_con_pocas_ejecuciones_anotadas_no_se_ensena_un_porcentaje():
    """Es la misma guarda del 94 % sacado de cuatro casos, aplicada por versión.

    Si esta pantalla reescribiera el umbral por su cuenta, el día que alguien lo afinase
    en Evaluaciones aquí seguiría saliendo un porcentaje inventado.
    """
    metrica = prompts.metrics_for(
        _version(2, "Eres breve."),
        _uso(2, trazas=40, coste_por_traza=0.001),
        {"human": (3, 1)},
    )
    humana = next(r for r in metrica.rates if r.source == "human")
    assert humana.value is None
    assert "4 casos" in humana.unavailable
    assert (humana.passed, humana.judged) == (3, 4)
    # Y la línea de la versión enseña los casos en bruto, no un porcentaje.
    assert "3/4" in metrica.headline


def test_con_casos_suficientes_la_linea_junta_coste_y_acierto():
    """La frase del encargo: «v8: 0,004 $/ejecución, 94 %»."""
    metrica = prompts.metrics_for(
        _version(8, "Eres breve."),
        _uso(8, trazas=50, coste_por_traza=0.004),
        {"human": (47, 3)},
    )
    assert "v8" in metrica.headline
    assert "0,004 US$" in metrica.headline
    assert "94 %" in metrica.headline


def test_una_version_sin_trafico_vale_none_y_no_cero():
    """Cero se lee como «no cuesta nada», que es otra afirmación y normalmente falsa."""
    metrica = prompts.metrics_for(_version(3, "x"), None, {})
    assert metrica.cost_per_execution_usd is None
    assert metrica.traces == 0
    assert "no ha corrido ninguna ejecución" in metrica.cost_unavailable


def test_dos_versiones_con_margenes_solapados_no_tienen_ganador():
    """Declarar ganador sobre una diferencia que cabe dentro del margen es exactamente
    cómo se deja puesta una regresión creyendo que es una mejora."""
    a = prompts.metrics_for(
        _version(7, "x"), _uso(7, trazas=30, coste_por_traza=0.007), {"human": (24, 6)}
    )
    b = prompts.metrics_for(
        _version(8, "y"), _uso(8, trazas=30, coste_por_traza=0.004), {"human": (25, 5)}
    )
    comparacion = prompts.compare_versions(a, b)

    humano = next(c for c in comparacion.by_source if c.source == "human")
    assert humano.verdict == "empate"
    assert "no se distinguen" in humano.headline
    # Y el titular junta las dos cosas: acierta igual y cuesta menos.
    assert "acierta igual" in comparacion.headline
    assert "menos por ejecución" in comparacion.headline


def test_una_version_que_empeora_manda_sobre_el_ahorro():
    a = prompts.metrics_for(
        _version(7, "x"), _uso(7, trazas=60, coste_por_traza=0.01), {"human": (58, 2)}
    )
    b = prompts.metrics_for(
        _version(8, "y"), _uso(8, trazas=60, coste_por_traza=0.001), {"human": (20, 40)}
    )
    comparacion = prompts.compare_versions(a, b)
    assert "acierta menos" in comparacion.headline
    assert comparacion.headline.index("acierta menos") < comparacion.headline.index("cuesta")


def test_el_coste_no_se_compara_en_porcentaje_con_cuatro_ejecuciones():
    """El coste no lleva margen porque es una factura, pero un cociente sobre tres
    ejecuciones sigue siendo la diferencia entre dos anécdotas."""
    a = prompts.metrics_for(_version(7, "x"), _uso(7, trazas=3, coste_por_traza=0.01), {})
    b = prompts.metrics_for(_version(8, "y"), _uso(8, trazas=40, coste_por_traza=0.001), {})
    comparacion = prompts.compare_versions(a, b)
    assert comparacion.cost_change is None
    assert "anécdotas" in comparacion.cost_unavailable


def test_el_acierto_de_persona_y_de_juez_no_se_funden():
    """La misma regla estructural de D-083, aquí también: dos bloques, nunca una media."""
    metrica = prompts.metrics_for(
        _version(8, "x"),
        _uso(8, trazas=40, coste_por_traza=0.001),
        {"human": (10, 10), "llm_judge": (20, 0)},
    )
    valores = {r.source: r.value for r in metrica.rates}
    assert valores["human"] == pytest.approx(0.5)
    assert valores["llm_judge"] == pytest.approx(1.0)
    assert len(metrica.rates) == 2


# ---------------------------------------------------------------------------------
# El tráfico va a la versión que lo produjo
# ---------------------------------------------------------------------------------


def test_el_texto_de_reserva_no_cuenta_como_la_version_de_produccion():
    """Si se sumara, el coste de producción incluiría los ratos en los que Laplace no
    respondía y el agente corrió con el texto del código. Es tráfico real, pero de otra
    cosa, y mezclarlo falsea justo la cifra que se mira."""
    card = prompts.build_card(
        _prompt(produccion=2),
        [_version(1, "viejo"), _version(2, "nuevo")],
        [
            _uso(2, trazas=20, coste_por_traza=0.002),
            _uso(0, trazas=5, coste_por_traza=0.009),
            _uso(1, trazas=20, coste_por_traza=0.004),
        ],
        {},
        [],
    )
    produccion = next(v for v in card.versions if v.version == 2)
    reserva = next(v for v in card.versions if v.label == "reserva")
    assert produccion.traces == 20
    assert reserva.traces == 5
    assert card.fallback_traces == 5
    # La reserva va la última y no se puede desplegar: no es una versión guardada.
    assert card.versions[-1].label == "reserva"


def test_se_compara_contra_la_anterior_con_trafico_no_contra_un_borrador():
    """Tres borradores guardados y sólo el último desplegado: comparar contra un
    borrador que no corrió nunca sería comparar contra el vacío (D-080 otra vez)."""
    card = prompts.build_card(
        _prompt(produccion=3),
        [_version(1, "a"), _version(2, "b"), _version(3, "c")],
        [
            _uso(1, trazas=30, coste_por_traza=0.01),
            _uso(3, trazas=30, coste_por_traza=0.002),
        ],
        {},
        [],
    )
    assert card.comparison is not None
    assert (card.comparison.a_version, card.comparison.b_version) == (1, 3)


def test_sin_otra_version_con_trafico_se_dice_en_vez_de_comparar():
    card = prompts.build_card(
        _prompt(produccion=2),
        [_version(1, "a"), _version(2, "b")],
        [_uso(2, trazas=30, coste_por_traza=0.002)],
        {},
        [],
    )
    assert card.comparison is None
    assert "ninguna otra versión con tráfico" in card.comparison_unavailable


def test_despues_de_volver_atras_se_sigue_comparando_con_la_nueva():
    """Justo después de un rollback, la de producción es la vieja y la única con
    tráfico al lado es la que se acaba de quitar. Si sólo se mirase hacia atrás, la
    pantalla se quedaría muda en el momento exacto en que alguien quiere ver qué ha
    hecho."""
    card = prompts.build_card(
        _prompt(produccion=7),
        [_version(7, "vieja"), _version(8, "nueva")],
        [
            _uso(7, trazas=30, coste_por_traza=0.007),
            _uso(8, trazas=30, coste_por_traza=0.004),
        ],
        {},
        [],
    )
    assert card.comparison is not None
    # El par se ordena por número: la frase habla de la nueva respecto de la vieja,
    # esté puesta la que esté.
    assert (card.comparison.a_version, card.comparison.b_version) == (7, 8)
    assert "menos por ejecución" in card.comparison.headline


def test_el_coste_de_una_version_es_un_suelo_si_hay_pasos_sin_tarifa():
    metrica = prompts.metrics_for(
        _version(2, "x"),
        _uso(2, trazas=10, coste_por_traza=0.001, unknown_cost_spans=3),
        {},
    )
    assert metrica.cost_is_floor is True
    assert metrica.headline.startswith("v2 · ≥ ")


def test_el_veredicto_se_cruza_por_traza_y_una_traza_puede_tener_varias_versiones():
    anotaciones = [
        Annotation(id="a1", trace_id="t1", source="human", verdict="fail", created_at=AHORA),
        Annotation(id="a2", trace_id="t2", source="human", verdict="pass", created_at=AHORA),
    ]
    por_traza = {"t1": [("resumen", 8), ("clasificar", 2)], "t2": [("resumen", 8)]}
    veredictos = prompts.verdicts_by_version(anotaciones, por_traza)

    assert veredictos[("resumen", 8)]["human"] == (1, 1)
    assert veredictos[("clasificar", 2)]["human"] == (0, 1)


def test_una_anotacion_sin_veredicto_no_cuenta_para_nadie():
    anotaciones = [
        Annotation(id="a1", trace_id="t1", source="human", verdict="unknown", created_at=AHORA)
    ]
    veredictos = prompts.verdicts_by_version(anotaciones, {"t1": [("resumen", 8)]})
    assert veredictos == {}


# ---------------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------------


def test_el_diff_dice_que_cambio_y_cuanto():
    diff = prompts.diff_versions(
        "Eres un asistente.\nResponde en español.\nSé breve.",
        "Eres un asistente.\nResponde en español y en tercera persona.\nSé breve.\nNo inventes.",
    )
    assert (diff.added, diff.removed, diff.unchanged) == (2, 1, 2)
    assert "2 líneas añadidas" in diff.summary and "1 línea quitada" in diff.summary
    # Las líneas iguales llevan numeración de los dos lados; las nuevas, sólo del suyo.
    iguales = [line for line in diff.lines if line.op == "="]
    assert all(line.left and line.right for line in iguales)
    assert all(line.left is None for line in diff.lines if line.op == "+")


def test_dos_versiones_identicas_lo_dicen_en_vez_de_ensenar_un_diff_vacio():
    diff = prompts.diff_versions("igual", "igual")
    assert diff.summary == "sin cambios en el texto"
    assert diff.added == diff.removed == 0


# ---------------------------------------------------------------------------------
# Atribución de picos: con trazas, no con la hora del despliegue (D-092)
# ---------------------------------------------------------------------------------


def test_una_version_de_prompt_nueva_en_el_tramo_es_una_causa():
    dentro = WindowFacts(traces=10, cost_usd=1.0, prompts={"resumen@8"})
    antes = WindowFacts(traces=100, cost_usd=2.0, prompts={"resumen@7"})
    causas, sin_atribuir = attribute(dentro, antes, 0.5, None)

    assert [c.kind for c in causas] == ["version_prompt"]
    assert "v8" in causas[0].text
    assert causas[0].link == {"prompt": "resumen", "version": "8"}
    assert sin_atribuir == ""


def test_una_version_que_ya_estaba_antes_no_es_una_causa():
    """Si se señalara igual, cualquier pico de un agente con prompts gestionados
    tendría siempre como causa «la versión que lleva puesta desde hace un mes»."""
    dentro = WindowFacts(traces=10, cost_usd=1.0, prompts={"resumen@8"})
    antes = WindowFacts(traces=100, cost_usd=2.0, prompts={"resumen@8"})
    causas, sin_atribuir = attribute(dentro, antes, 0.5, None)

    assert causas == []
    assert "ninguna versión de prompt" in sin_atribuir


def test_el_trafico_de_reserva_en_un_pico_se_nombra_por_lo_que_es():
    dentro = WindowFacts(traces=10, cost_usd=1.0, prompts={"resumen@0"})
    antes = WindowFacts(traces=100, cost_usd=2.0, prompts={"resumen@8"})
    causas, _ = attribute(dentro, antes, 0.5, None)
    assert "texto de reserva" in causas[0].text


def test_la_version_de_prompt_se_nombra_antes_que_el_modelo_nuevo():
    """De todo lo que puede cambiar alrededor de un pico, el prompt es lo más
    accionable: hay un botón para deshacerlo."""
    dentro = WindowFacts(traces=10, cost_usd=1.0, prompts={"resumen@8"}, models={"caro"})
    antes = WindowFacts(traces=100, cost_usd=2.0, prompts={"resumen@7"}, models={"barato"})
    causas, _ = attribute(dentro, antes, 0.5, None)
    assert [c.kind for c in causas] == ["version_prompt", "modelo_nuevo"]


# ---------------------------------------------------------------------------------
# El modo degradado (D-093)
# ---------------------------------------------------------------------------------


def _observado(
    clave: str, pista: str, trazas: int, sitio: str = "atender > responder"
) -> ObservedPrompt:
    return ObservedPrompt(
        step_key=clave,
        step_label="responder",
        site=sitio,
        hint=pista,
        traces=trazas,
        calls=trazas,
        cost_usd=0.001 * trazas,
        first_seen=AHORA,
        last_seen=AHORA + timedelta(hours=1),
    )


def test_sin_gestionar_nada_dos_huellas_del_mismo_paso_son_dos_versiones():
    """La identidad de un paso ya incluye la huella de sus instrucciones (D-060), así
    que esto sale gratis y es lo que impide que la pestaña se quede en blanco."""
    pasos = prompts.observed_steps(
        [
            _observado("k1", "Eres un asistente breve.", 40),
            _observado("k2", "Eres un asistente.", 60),
        ]
    )
    assert len(pasos) == 1
    assert pasos[0].label == "responder"
    assert len(pasos[0].variants) == 2
    assert pasos[0].variants[0].cost_per_execution_usd == pytest.approx(0.001)


def test_un_prompt_con_datos_variables_dentro_no_son_doscientas_versiones():
    """La fragilidad conocida de la identidad de paso, dicha en pantalla en vez de
    convertida en basura con aspecto de dato."""
    pasos = prompts.observed_steps(
        [_observado(f"k{i}", f"Hoy es {i} de marzo.", 1) for i in range(30)]
    )
    assert pasos[0].unstable is True
    assert pasos[0].variants == []
    assert "datos variables" in pasos[0].note
    assert "30 variantes" in pasos[0].note


def test_dos_llamantes_del_mismo_paso_no_son_dos_versiones_de_su_prompt():
    """El fallo que D-106 dejó detrás sin que nadie lo viera (D-115).

    Desde que la identidad de un paso incluye el CAMINO de llamada, dos `step_key` bajo
    la misma etiqueta pueden ser el mismo prompt llamado desde dos sitios. La pantalla
    seguía contándolos como versiones: «redactar» salía con «3 juegos de instrucciones»
    y las tres filas enseñaban el mismo texto carácter por carácter. Afirmar un cambio
    de prompt que no ocurrió es exactamente lo que esta pestaña no puede hacer.

    Lo correcto no es fundirlos —dos llamantes son dos pasos, que es justo lo que D-106
    consiguió distinguir— sino que ninguno de los dos declare una versión que no tuvo, y
    que se puedan decir por separado en vez de leerse como un duplicado.
    """
    pasos = prompts.observed_steps(
        [
            _observado("k1", "Responde breve.", 10, sitio="atender > responder"),
            _observado("k2", "Responde breve.", 10, sitio="resumir > responder"),
        ]
    )
    assert [len(paso.variants) for paso in pasos] == [1, 1], (
        "el prompt no cambió en ninguno de los dos caminos, así que ninguno puede "
        f"declarar más de un juego de instrucciones: {[len(p.variants) for p in pasos]}"
    )
    etiquetas = sorted(paso.label for paso in pasos)
    assert etiquetas == ["atender → responder", "resumir → responder"], (
        f"dos bloques titulados igual se leen como un duplicado: {etiquetas}"
    )


def test_el_trafico_viejo_sin_camino_no_inventa_versiones():
    """El caso que obliga a fusionar por prompt además de agrupar por camino.

    Las trazas anteriores a D-106 llegaron sin `step_site`, así que dos llamantes caen
    en el mismo grupo y sólo se distinguen por la clave. Ahí, enseñar dos filas con el
    mismo texto sería afirmar un cambio que no hubo: se fusionan y el tráfico se suma.
    """
    pasos = prompts.observed_steps(
        [
            _observado("k1", "Responde breve.", 10, sitio=""),
            _observado("k2", "Responde breve.", 10, sitio=""),
        ]
    )
    assert len(pasos) == 1
    assert len(pasos[0].variants) == 1, "el mismo texto dos veces no son dos versiones"
    assert pasos[0].variants[0].traces == 20, "y el tráfico de los dos se suma"


def test_un_prompt_que_si_cambia_bajo_el_mismo_llamante_son_dos_versiones():
    """El contraejemplo, que es lo que impide arreglar lo de arriba de más.

    Si el prompt cambia dentro del mismo camino, son dos juegos de instrucciones y hay
    que poder verlos por separado con sus fechas: fusionarlos escondería justo lo que
    esta pestaña existe para enseñar.
    """
    pasos = prompts.observed_steps(
        [
            _observado("k1", "Responde breve.", 10, sitio="atender > responder"),
            _observado("k2", "Responde largo y con detalle.", 10, sitio="atender > responder"),
        ]
    )
    assert len(pasos) == 1
    assert len(pasos[0].variants) == 2
    assert {v.hint for v in pasos[0].variants} == {
        "Responde breve.",
        "Responde largo y con detalle.",
    }


def test_tres_llamadas_de_la_misma_ejecucion_no_son_tres_versiones():
    """El fallo que se vio en pantalla con la demo, en septiembre.

    `responder` llama tres veces al modelo desde el mismo sitio —clasificar, extraer y
    contestar—, así que las tres huellas comparten camino. La pestaña decía «3 juegos
    de instrucciones» y «dos instrucciones distintas bajo el mismo paso son un cambio de
    prompt», con las mismas dieciséis ejecuciones y las mismas fechas en las tres filas.
    No hubo cambio: son tres llamadas que corren juntas en cada ejecución.

    Lo que lo distingue no es el camino ni la fecha —un despliegue gradual también
    solapa fechas— sino que aparezcan en la MISMA ejecución: una versión de un prompt no
    convive con la anterior dentro de una sola traza.
    """
    filas = [
        _observado("k1", "Clasifica la intención del usuario.", 16),
        _observado("k2", "Devuelve origen y destino en JSON.", 16),
        _observado("k3", "Responde usando exclusivamente este manual.", 16),
    ]
    pasos = prompts.observed_steps(filas, simultaneous={"k1", "k2", "k3"})

    assert len(pasos) == 1
    paso = pasos[0]
    assert paso.concurrent is True, "corren juntas: no se puede hablar de versiones"
    assert len(paso.variants) == 3, "las tres llamadas se siguen viendo, cada una con su coste"
    assert "versiones" in paso.note and "misma ejecución" in paso.note, paso.note


def test_sin_convivir_en_una_ejecucion_siguen_siendo_versiones():
    """El contraejemplo: si las dos huellas no coinciden nunca en una traza, sí son un
    cambio de prompt, y marcarlas como simultáneas escondería lo que la pestaña enseña."""
    pasos = prompts.observed_steps(
        [
            _observado("k1", "Responde breve.", 10),
            _observado("k2", "Responde largo y con detalle.", 10),
        ],
        simultaneous=set(),
    )
    assert pasos[0].concurrent is False
    assert pasos[0].note == ""


def test_la_guarda_de_la_plantilla_cuenta_prompts_y_no_llamantes():
    """D-093 se disparaba por el motivo equivocado.

    «Más de ocho variantes es una plantilla con datos dentro» dejó de ser cierto cuando
    un paso llamado desde nueve sitios producía nueve claves con el mismo prompt. Es el
    único sitio del producto donde se nombra la huella partida, así que dispararlo mal
    lo deja inservible.
    """
    pasos = prompts.observed_steps(
        [
            _observado(f"k{i}", "Responde breve.", 1, sitio=f"sitio-{i} > responder")
            for i in range(12)
        ]
    )
    assert pasos[0].unstable is False, (
        "doce llamantes del mismo prompt no son una plantilla con datos dentro"
    )


# ---------------------------------------------------------------------------------
# El ciclo entero, por la API y contra SQLite
# ---------------------------------------------------------------------------------


def _traza(project: str, trace_id: str, coste: float, prompt: str, version: int) -> list[Span]:
    raiz = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        project_id=project,
        name="responder",
        type="agent",
        status="ok",
        start_time=AHORA,
        end_time=AHORA + timedelta(seconds=1),
        duration_ms=1000.0,
    )
    llm = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=raiz.span_id,
        project_id=project,
        name="chat",
        type="llm",
        status="ok",
        start_time=AHORA,
        end_time=AHORA + timedelta(milliseconds=500),
        duration_ms=500.0,
        step_key=f"paso-{prompt}",
        step_label="responder",
        step_hint=f"instrucciones de {prompt} v{version}",
        prompt_name=prompt,
        prompt_version=version,
    )
    llm.llm = LLMAttributes(
        request_model="gpt-5.6-luna",
        usage=TokenUsage(input_tokens=800, output_tokens=80),
        cost=Cost(input_usd=coste * 0.8, output_usd=coste * 0.2, total_usd=coste),
    )
    return [raiz, llm]


@pytest.fixture
def local(tmp_path, monkeypatch):
    """La aplicación tal cual la arranca `laplace ui`, con tráfico de dos versiones."""
    db = tmp_path / "laplace.db"
    store = SQLiteStore(db)
    store.migrate()
    spans: list[Span] = []
    for i in range(20):
        spans += _traza("local", f"v7-{i:02d}", 0.007, "resumen", 7)
    for i in range(20):
        spans += _traza("local", f"v8-{i:02d}", 0.004, "resumen", 8)
    store.insert_spans(spans)

    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(db))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")

    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client
    config.get_settings.cache_clear()


def test_el_ciclo_entero_en_local(local):
    """Crear → servir al SDK → versión nueva → desplegar → volver atrás."""
    creado = local.post(
        "/api/prompts",
        json={"project_id": "local", "name": "resumen", "text": "Eres un asistente."},
    )
    assert creado.status_code == 200, creado.text
    prompt_id = creado.json()["id"]
    # La primera versión se despliega sola: si no, el primer get_prompt() sería un 409.
    assert creado.json()["production_version"] == 1

    servido = local.get(
        "/api/prompts/resolve", params={"project_id": "local", "name": "resumen"}
    ).json()
    assert servido["version"] == 1
    assert servido["text"] == "Eres un asistente."

    nueva = local.post(
        f"/api/prompts/{prompt_id}/versions",
        json={"project_id": "local", "text": "Eres un asistente breve."},
    ).json()
    assert nueva["version"] == 2

    # Guardar NO despliega: el SDK sigue sirviendo la 1.
    assert (
        local.get(
            "/api/prompts/resolve", params={"project_id": "local", "name": "resumen"}
        ).json()["version"]
        == 1
    )

    puesta = local.post(
        f"/api/prompts/{prompt_id}/production", json={"project_id": "local", "version": 2}
    )
    assert puesta.status_code == 200
    assert puesta.json()["deploy"]["rollback"] is False
    assert "un minuto" in puesta.json()["detail"]

    # Y el rollback de un clic, que queda marcado como tal en el historial.
    vuelta = local.post(
        f"/api/prompts/{prompt_id}/production", json={"project_id": "local", "version": 1}
    )
    assert vuelta.json()["deploy"]["rollback"] is True
    ficha = local.get(
        f"/api/prompts/{prompt_id}", params={"project_id": "local"}
    ).json()
    assert [d["version"] for d in ficha["deploys"]] == [1, 2, 1]


def test_las_metricas_salen_del_trafico_real(local):
    """Lo que hace que esta pestaña sea de Laplace y no de cualquiera."""
    creado = local.post(
        "/api/prompts",
        json={"project_id": "local", "name": "resumen", "text": "v1"},
    ).json()
    prompt_id = creado["id"]
    for _ in range(6):  # hasta la v7
        local.post(
            f"/api/prompts/{prompt_id}/versions", json={"project_id": "local", "text": "más"}
        )
    local.post(
        f"/api/prompts/{prompt_id}/versions",
        json={"project_id": "local", "text": "v8", "deploy": True},
    )

    vista = local.get("/api/prompts", params={"project_id": "local", "days": 7}).json()
    assert vista["managed"] is True
    card = vista["prompts"][0]
    por_version = {v["version"]: v for v in card["versions"]}

    # 0,007 $ y 0,004 $ por ejecución, medidos sobre las trazas sembradas.
    assert por_version[7]["cost_per_execution_usd"] == pytest.approx(0.007)
    assert por_version[8]["cost_per_execution_usd"] == pytest.approx(0.004)
    assert por_version[8]["traces"] == 20
    assert por_version[8]["in_production"] is True
    # Y el titular junta las dos cosas aunque todavía no haya acierto que enseñar.
    assert "menos por ejecución" in card["comparison"]["headline"]


def test_el_acierto_por_version_sale_de_las_anotaciones_de_esas_trazas(local):
    creado = local.post(
        "/api/prompts", json={"project_id": "local", "name": "resumen", "text": "v1"}
    ).json()
    for _ in range(7):
        local.post(
            f"/api/prompts/{creado['id']}/versions",
            json={"project_id": "local", "text": "más", "deploy": True},
        )

    # Doce ejecuciones de la v8 anotadas, once bien.
    for i in range(12):
        local.post(
            "/api/annotations",
            json={
                "project_id": "local",
                "trace_id": f"v8-{i:02d}",
                "verdict": "pass" if i < 11 else "fail",
            },
        )

    vista = local.get("/api/prompts", params={"project_id": "local", "days": 7}).json()
    v8 = next(
        v for v in vista["prompts"][0]["versions"] if v["version"] == 8
    )
    humana = next(r for r in v8["rates"] if r["source"] == "human")
    assert (humana["passed"], humana["judged"]) == (11, 12)
    assert humana["value"] == pytest.approx(11 / 12)
    # Las ocho ejecuciones no anotadas no cuentan como fallo.
    assert humana["unjudged"] == 8


def test_las_tiradas_de_evaluacion_no_cuentan_en_la_pestana(local):
    """D-160: el coste y el acierto de una versión son los de su tráfico real. Una tirada
    A/B con la v8 y otro modelo abarataba la v8 aquí y no en el Diagnóstico."""
    import os

    from laplace.semconv import EVAL_TAG

    creado = local.post(
        "/api/prompts", json={"project_id": "local", "name": "resumen", "text": "v1"}
    ).json()
    for _ in range(7):
        local.post(
            f"/api/prompts/{creado['id']}/versions",
            json={"project_id": "local", "text": "más", "deploy": True},
        )
    # Diez ejecuciones de una tirada con la v8, mucho más baratas, anotadas como fallo.
    tirada: list[Span] = []
    for i in range(10):
        spans = _traza("local", f"eval-{i:02d}", 0.0001, "resumen", 8)
        spans[0].tags = [EVAL_TAG]
        tirada += spans
    SQLiteStore(Path(os.environ["LAPLACE_SQLITE_PATH"])).insert_spans(tirada)
    for i in range(10):
        local.post(
            "/api/annotations",
            json={"project_id": "local", "trace_id": f"eval-{i:02d}", "verdict": "fail"},
        )

    vista = local.get("/api/prompts", params={"project_id": "local", "days": 7}).json()
    v8 = next(v for v in vista["prompts"][0]["versions"] if v["version"] == 8)
    assert v8["traces"] == 20
    assert v8["cost_per_execution_usd"] == pytest.approx(0.004)
    humana = next(r for r in v8["rates"] if r["source"] == "human")
    assert humana["judged"] == 0, "los veredictos de la tirada no son del tráfico real"


def test_sin_prompts_gestionados_la_pestana_ensena_lo_de_las_trazas(local):
    """Degradarse con dignidad: menos de lo que da adoptar la gestión, pero no una
    pantalla en blanco (D-093)."""
    vista = local.get("/api/prompts", params={"project_id": "local", "days": 7}).json()
    assert vista["managed"] is False
    assert vista["prompts"] == []
    assert vista["observed"], "tiene que quedar algo que enseñar"
    assert vista["observed"][0]["label"] == "responder"


def test_pedir_un_prompt_que_no_existe_dice_como_arreglarlo(local):
    respuesta = local.get(
        "/api/prompts/resolve", params={"project_id": "local", "name": "no-existe"}
    )
    assert respuesta.status_code == 404
    assert "fallback=" in respuesta.json()["detail"]


def test_no_se_pueden_crear_dos_prompts_con_el_mismo_nombre(local):
    cuerpo = {"project_id": "local", "name": "resumen", "text": "x"}
    assert local.post("/api/prompts", json=cuerpo).status_code == 200
    repetido = local.post("/api/prompts", json=cuerpo)
    assert repetido.status_code == 409
    assert "ya hay un prompt" in repetido.json()["detail"]


def test_la_comparacion_de_evaluaciones_dice_con_que_prompt_corrio_cada_lado(local):
    """La conexión con Evaluaciones: de qué versión era cada lado, leído de las trazas
    y no de la etiqueta que alguien le puso a la tirada (D-094)."""
    conjunto = local.post(
        "/api/datasets",
        json={"project_id": "local", "name": "ciclo", "filter": {}, "limit": 40},
    ).json()
    casos = local.get(f"/api/datasets/{conjunto['id']}").json()["items"]
    por_version = {
        7: [c for c in casos if c["trace_id"].startswith("v7-")],
        8: [c for c in casos if c["trace_id"].startswith("v8-")],
    }

    tiradas = {}
    for version, seleccion in por_version.items():
        tiradas[version] = local.post(
            "/api/runs",
            json={
                "project_id": "local",
                "dataset_id": conjunto["id"],
                "variant": f"prompt-v{version}",
                "items": [{"case_id": c["id"], "trace_id": c["trace_id"]} for c in seleccion],
            },
        ).json()

    comparacion = local.get(
        "/api/experiments/compare",
        params={"project_id": "local", "a": tiradas[7]["id"], "b": tiradas[8]["id"]},
    ).json()
    assert comparacion["a"]["prompt_versions"] == ["resumen v7"]
    assert comparacion["b"]["prompt_versions"] == ["resumen v8"]


# ---------------------------------------------------------------------------------
# Que actualizar Laplace no rompa una base que ya existía
# ---------------------------------------------------------------------------------


def test_una_base_sqlite_anterior_se_pone_al_dia_sola(tmp_path):
    """`CREATE TABLE IF NOT EXISTS` no toca una tabla que ya está.

    Sin la migración de columnas, alguien con un `~/.laplace/laplace.db` de hace dos
    semanas actualizaría Laplace y se encontraría un «no such column: prompt_name» que
    no puede arreglar sin borrar sus trazas.
    """
    import sqlite3

    db = tmp_path / "vieja.db"
    store = SQLiteStore(db)
    store.migrate()
    with sqlite3.connect(db) as conn:
        conn.execute("DROP INDEX idx_spans_prompt")
        conn.execute("ALTER TABLE spans DROP COLUMN prompt_name")
        conn.execute("ALTER TABLE spans DROP COLUMN prompt_version")

    otra = SQLiteStore(db)
    otra.migrate()
    otra.insert_spans(_traza("p", "t1", 0.001, "resumen", 3))
    uso = otra.prompt_usage(
        "p", Window(since=AHORA - timedelta(days=1), until=AHORA + timedelta(days=1), days=2)
    )
    assert [(u.name, u.version, u.traces) for u in uso] == [("resumen", 3, 1)]


def test_las_dos_bases_de_metadatos_guardan_un_prompt_igual(tmp_path):
    """La paridad de D-084, extendida a los prompts: si esto se rompe, el modo local y
    el de nube dejan de ser el mismo producto sin que nadie lo note."""
    store = SQLiteMetadataStore(tmp_path / "m.db")
    store.migrate()
    store.create_prompt(_prompt(produccion=None))
    primera = store.add_prompt_version("pr_1", "uno", author="enol")
    segunda = store.add_prompt_version("pr_1", "dos", author="enol")
    assert (primera.version, segunda.version) == (1, 2)

    store.set_prompt_production("pr_1", 2, actor="enol")
    vuelta = store.set_prompt_production("pr_1", 1, actor="enol", note="mejor la de antes")
    assert vuelta.rollback is True
    assert store.get_prompt("pr_1").production_version == 1
    assert store.get_prompt_version("pr_1", 2).text == "dos"

    # Y borrar el prompt se lleva versiones e historial, pero nunca las trazas.
    assert store.delete_prompt("pr_1") is True
    assert store.list_prompt_versions("pr_1") == []

# ---------------------------------------------------------------------------------
# El SDK: qué se marca en la traza, y qué pasa cuando Laplace no responde
# ---------------------------------------------------------------------------------


class _SpanFalso:
    """Un span con la forma justa que usa `record_prompt`: guardar atributos."""

    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


@pytest.fixture(autouse=True)
def _sin_cache_de_prompts():
    """Cada prueba del SDK empieza sin caché: si no, se contaminan entre ellas."""
    from laplace import prompts as sdk

    sdk.clear_cache()
    yield
    sdk.clear_cache()


def test_el_sdk_marca_la_llamada_solo_si_el_texto_va_de_verdad_en_los_mensajes():
    """La atribución se comprueba, no se deduce.

    Fiarse del último `get_prompt()` haría que un agente que pide un prompt y llama al
    modelo con otro texto le colgara tráfico ajeno a esa versión, y como las métricas de
    la pestaña son por versión, nadie lo notaría nunca.
    """
    from laplace import prompts as sdk
    from laplace import semconv
    from laplace.integrations._common import record_prompt

    servido = sdk.ServedPrompt(name="resumen", version=8, text="Eres un asistente breve.")
    servido.render()

    marcado = _SpanFalso()
    record_prompt(marcado, [{"role": "system", "content": "Eres un asistente breve."}])
    assert marcado.attributes[semconv.LAPLACE_PROMPT_NAME] == "resumen"
    assert marcado.attributes[semconv.LAPLACE_PROMPT_VERSION] == 8

    # El mismo contexto, pero una llamada que manda otra cosa: no se marca nada.
    otro = _SpanFalso()
    record_prompt(otro, [{"role": "system", "content": "Eres un pirata."}])
    assert otro.attributes == {}


def test_el_prompt_reindentado_sigue_siendo_el_mismo_prompt():
    """Los SDK de los proveedores y el propio código del usuario reindentan texto sin
    avisar. Comparar byte a byte dejaría sin marcar media pestaña."""
    from laplace import prompts as sdk
    from laplace import semconv
    from laplace.integrations._common import record_prompt

    sdk.ServedPrompt(name="resumen", version=3, text="Eres  un\n  asistente breve.").render()
    marcado = _SpanFalso()
    record_prompt(marcado, [{"role": "system", "content": "Eres un asistente breve."}])
    assert marcado.attributes[semconv.LAPLACE_PROMPT_VERSION] == 3


def test_una_variable_sin_valor_revienta_en_vez_de_mandarse_al_modelo():
    """Mandar `{{nombre}}` literal al modelo cuesta dinero y devuelve basura. De los dos
    fallos posibles, el que se ve en el acto es infinitamente más barato."""
    from laplace.prompts import PromptError, ServedPrompt

    servido = ServedPrompt(name="saludo", version=1, text="Hola {{nombre}}, soy {{quien}}.")
    assert servido.render(nombre="Ana", quien="Laplace") == "Hola Ana, soy Laplace."
    with pytest.raises(PromptError) as excinfo:
        servido.render(nombre="Ana")
    assert "quien" in str(excinfo.value)


def test_si_laplace_no_responde_se_sirve_la_copia_guardada(monkeypatch):
    """Pedir el prompt mete a Laplace en el camino caliente del agente. Una excepción
    ahí es una petición rota de un usuario final de otra empresa (D-091)."""
    from laplace import prompts as sdk

    llamadas = {"n": 0}

    def _falso(url, **kwargs):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return {"name": "resumen", "version": 8, "text": "Eres breve.", "prompt_id": "pr_1"}
        raise sdk.LaplaceHTTPError("no se puede hablar con Laplace")

    monkeypatch.setattr(sdk, "request", _falso)
    monkeypatch.setattr(sdk, "endpoint", lambda *_: "http://laplace")

    primero = sdk.get_prompt("resumen", project="p", ttl_seconds=0)
    assert (primero.version, primero.source) == (8, "laplace")

    # Segunda llamada: el backend se ha caído y la caché está vencida a propósito.
    segundo = sdk.get_prompt("resumen", project="p", ttl_seconds=0)
    assert segundo.version == 8
    assert segundo.source == "cache"


def test_sin_copia_ni_reserva_se_levanta_un_error_que_dice_como_arreglarlo(monkeypatch):
    from laplace import prompts as sdk

    def _falso(url, **kwargs):
        raise sdk.LaplaceHTTPError("404 en /api/prompts/resolve")

    monkeypatch.setattr(sdk, "request", _falso)
    monkeypatch.setattr(sdk, "endpoint", lambda *_: "http://laplace")

    with pytest.raises(sdk.PromptError) as excinfo:
        sdk.get_prompt("resumen", project="p")
    assert "fallback=" in str(excinfo.value)


def test_el_texto_de_reserva_se_registra_como_reserva_y_no_como_produccion(monkeypatch):
    """Versión 0, no la que estuviera en producción. Ese tráfico es real y no salió de
    ninguna versión guardada: sumarlo a la de producción falsearía sus métricas."""
    from laplace import prompts as sdk
    from laplace import semconv
    from laplace.integrations._common import record_prompt

    def _falso(url, **kwargs):
        raise sdk.LaplaceHTTPError("no se puede hablar con Laplace")

    monkeypatch.setattr(sdk, "request", _falso)
    monkeypatch.setattr(sdk, "endpoint", lambda *_: "http://laplace")

    servido = sdk.get_prompt("resumen", project="p", fallback="Eres el del código.")
    assert (servido.version, servido.source) == (0, "fallback")

    marcado = _SpanFalso()
    record_prompt(marcado, [{"role": "system", "content": "Eres el del código."}])
    assert marcado.attributes[semconv.LAPLACE_PROMPT_NAME] == "resumen"
    assert marcado.attributes[semconv.LAPLACE_PROMPT_VERSION] == 0


def test_la_ingesta_copia_la_version_tal_cual_y_no_la_deduce():
    """La ingesta no inventa una versión que el emisor no haya afirmado (D-090)."""
    from laplace import semconv

    from laplace_backend.ingest.otlp import _entero

    assert _entero("8") == 8
    assert _entero(None) == 0
    assert _entero("no soy un número") == 0
    assert semconv.LAPLACE_PROMPT_VERSION == "laplace.prompt.version"

# ---------------------------------------------------------------------------------
# El SQL de la nube. Estas consultas son nuevas y el modo local no las prueba: un alias
# mal puesto o una función que tapa una columna sólo se ven ejecutándolas.
# ---------------------------------------------------------------------------------


def _nube():
    from laplace_backend.config import Settings
    from laplace_backend.storage.clickhouse import ClickHouseStore

    store = ClickHouseStore(Settings())
    if not store.health():
        pytest.skip("no hay ClickHouse escuchando; se omiten las pruebas de la nube")
    store.migrate()
    return store


def _ventana() -> Window:
    return Window(since=AHORA - timedelta(days=1), until=AHORA + timedelta(days=1), days=2)


@pytest.fixture
def sembrado(tmp_path):
    """Los mismos spans en los dos almacenes, y un proyecto propio de esta ejecución.

    Propio porque la base de desarrollo es la misma que mira el usuario en la interfaz y
    dejar proyectos `prompt-paridad-*` sembrados por cada ejecución es basura visible.
    """
    nube = _nube()
    project = f"prompt-paridad-{uuid.uuid4().hex[:8]}"
    local = SQLiteStore(tmp_path / "l.db")
    local.migrate()

    spans: list[Span] = []
    for i in range(4):
        spans += _traza(project, f"{project}-v7-{i}", 0.007, "resumen", 7)
    for i in range(3):
        spans += _traza(project, f"{project}-v8-{i}", 0.004, "resumen", 8)
    # Tráfico de reserva: versión 0. Tiene que salir como una fila más y no perderse.
    spans += _traza(project, f"{project}-res", 0.009, "resumen", 0)
    # Y una traza sin prompt gestionado, que no debe aparecer en `prompt_usage`.
    spans += _traza(project, f"{project}-suelto", 0.001, "", 0)

    local.insert_spans(spans)
    nube.insert_spans(spans)
    try:
        yield {"project": project, "local": local, "nube": nube}
    finally:
        nube.delete_project(project)


def test_el_uso_por_version_dice_lo_mismo_en_los_dos_almacenes(sembrado):
    """Paridad de D-066 sobre la consulta nueva: la pestaña de Prompts no puede decir
    una cosa en local y otra en la nube sobre los mismos spans."""
    ventana = _ventana()
    aqui = {
        (u.name, u.version): u
        for u in sembrado["local"].prompt_usage(sembrado["project"], ventana)
    }
    alli = {
        (u.name, u.version): u
        for u in sembrado["nube"].prompt_usage(sembrado["project"], ventana)
    }

    assert set(aqui) == set(alli) == {("resumen", 7), ("resumen", 8), ("resumen", 0)}
    for clave, uso in aqui.items():
        otro = alli[clave]
        assert uso.traces == otro.traces, clave
        assert uso.calls == otro.calls, clave
        assert uso.cost_usd == pytest.approx(otro.cost_usd, rel=1e-9), clave
        assert uso.input_tokens == otro.input_tokens, clave
        assert uso.unknown_cost_spans == otro.unknown_cost_spans, clave
        assert uso.first_seen == otro.first_seen, clave
        assert uso.last_seen == otro.last_seen, clave

    # Y las cifras son las sembradas: por ejecución, no por llamada.
    assert aqui[("resumen", 7)].traces == 4
    assert aqui[("resumen", 7)].cost_usd == pytest.approx(0.028)


def test_las_versiones_por_traza_dicen_lo_mismo_en_los_dos_almacenes(sembrado):
    ids = [f"{sembrado['project']}-v7-0", f"{sembrado['project']}-v8-1", "no-existe"]
    aqui = sembrado["local"].prompt_versions_by_trace(sembrado["project"], ids)
    alli = sembrado["nube"].prompt_versions_by_trace(sembrado["project"], ids)

    assert aqui == alli
    assert aqui[f"{sembrado['project']}-v7-0"] == [("resumen", 7)]
    assert "no-existe" not in aqui


def test_los_prompts_observados_dicen_lo_mismo_en_los_dos_almacenes(sembrado):
    """Es el modo degradado, que es justo el que verá quien no adopte nada."""
    ventana = _ventana()
    aqui = {
        o.step_key: o for o in sembrado["local"].observed_prompts(sembrado["project"], ventana)
    }
    alli = {
        o.step_key: o for o in sembrado["nube"].observed_prompts(sembrado["project"], ventana)
    }

    assert set(aqui) == set(alli)
    for clave, observado in aqui.items():
        otro = alli[clave]
        assert observado.step_label == otro.step_label, clave
        assert observado.hint == otro.hint, clave
        assert observado.traces == otro.traces, clave
        assert observado.cost_usd == pytest.approx(otro.cost_usd, rel=1e-9), clave
        assert observado.first_seen == otro.first_seen, clave


def test_las_llamadas_simultaneas_dicen_lo_mismo_en_los_dos_almacenes(sembrado):
    """Qué huellas conviven con otra del mismo camino dentro de una ejecución.

    Se siembra una traza con dos llamadas del mismo sitio y huellas distintas, que es la
    forma de la demo; las trazas de versiones de `sembrado` nunca mezclan dos huellas en
    la misma ejecución, así que no pueden salir.
    """
    project = sembrado["project"]
    raiz, llm = _traza(project, f"{project}-juntas", 0.002, "", 0)
    otra = Span(**{**llm.model_dump(), "span_id": uuid.uuid4().hex[:16]})
    llm.step_key, llm.step_site = "sitio-clasificar", "atender > responder"
    otra.step_key, otra.step_site = "sitio-contestar", "atender > responder"
    for almacen in (sembrado["local"], sembrado["nube"]):
        almacen.insert_spans([raiz, llm, otra])

    ventana = _ventana()
    aqui = sembrado["local"].co_occurring_step_keys(project, ventana)
    alli = sembrado["nube"].co_occurring_step_keys(project, ventana)
    assert aqui == alli == {"sitio-clasificar", "sitio-contestar"}


def test_la_version_de_prompt_llega_a_la_atribucion_de_picos_en_la_nube(sembrado):
    """`window_facts` es lo que alimenta la causa `version_prompt`. Si el `concat` de
    ClickHouse no produjera la misma etiqueta que el `||` de SQLite, el panel señalaría
    versiones distintas según el almacén."""
    dentro = (AHORA - timedelta(hours=1), AHORA + timedelta(hours=1))
    aqui = sembrado["local"].window_facts(sembrado["project"], *dentro)
    alli = sembrado["nube"].window_facts(sembrado["project"], *dentro)

    assert aqui.prompts == alli.prompts
    assert aqui.prompts == {"resumen@7", "resumen@8", "resumen@0"}


def test_un_span_sin_prompt_gestionado_no_aparece_como_version(sembrado):
    """El caso por defecto —nadie ha adoptado nada— no puede colarse como una versión
    vacía en la lista, que es lo que pasaría con un filtro mal puesto."""
    for almacen in ("local", "nube"):
        usos = sembrado[almacen].prompt_usage(sembrado["project"], _ventana())
        assert all(u.name for u in usos), almacen


def test_una_base_de_nube_anterior_se_pone_al_dia_sola():
    """El equivalente en la nube de la migración de SQLite: `CREATE TABLE IF NOT EXISTS`
    no toca una tabla que ya existe, así que las columnas nuevas entran por `ALTER`."""
    nube = _nube()
    filas = nube._client.query(
        "SELECT name FROM system.columns WHERE table = 'spans' AND database = currentDatabase()"
    ).result_rows
    columnas = {f[0] for f in filas}
    assert {"prompt_name", "prompt_version"} <= columnas


def test_los_dos_almacenes_de_metadatos_guardan_los_prompts_igual(tmp_path):
    """Paridad de lo mutable (D-084) con las tablas nuevas: prompts, versiones y
    despliegues tienen que leerse igual en local que en la nube."""
    from laplace_backend.config import Settings
    from laplace_backend.storage.postgres import PostgresMetadataStore

    nube = PostgresMetadataStore(Settings())
    if not nube.health():
        pytest.skip("no hay Postgres escuchando; no se puede comparar")
    nube.migrate()

    local = SQLiteMetadataStore(tmp_path / "m.db")
    local.migrate()
    project = f"prompt-meta-{uuid.uuid4().hex[:8]}"
    prompt_id = f"pr_{uuid.uuid4().hex[:8]}"

    salidas = {}
    for nombre, almacen in (("local", local), ("nube", nube)):
        almacen.create_prompt(
            Prompt(id=prompt_id, project_id=project, name="atencion", created_at=AHORA)
        )
        primera = almacen.add_prompt_version(prompt_id, "uno", notes="la primera", author="enol")
        segunda = almacen.add_prompt_version(prompt_id, "dos", author="enol")
        almacen.set_prompt_production(prompt_id, segunda.version, actor="enol")
        vuelta = almacen.set_prompt_production(
            prompt_id, primera.version, actor="enol", note="mejor la de antes"
        )
        salidas[nombre] = {
            "prompt": almacen.get_prompt(prompt_id),
            "versiones": almacen.list_prompt_versions(prompt_id),
            "despliegues": almacen.list_prompt_deploys(prompt_id),
            "vuelta": vuelta,
        }

    try:
        a, b = salidas["local"], salidas["nube"]
        assert a["prompt"].production_version == b["prompt"].production_version == 1
        assert a["prompt"].version_count == b["prompt"].version_count == 2
        assert [v.version for v in a["versiones"]] == [v.version for v in b["versiones"]] == [2, 1]
        assert [v.text for v in a["versiones"]] == [v.text for v in b["versiones"]]
        assert [v.notes for v in a["versiones"]] == [v.notes for v in b["versiones"]]
        # El número de versión lo calcula cada almacén por su cuenta: si uno empezara
        # en cero, el SDK pediría una versión que en el otro no existe.
        assert a["versiones"][-1].version == b["versiones"][-1].version == 1
        # Y la vuelta atrás se marca igual en los dos.
        assert a["vuelta"].rollback is b["vuelta"].rollback is True
        assert len(a["despliegues"]) == len(b["despliegues"]) == 2
    finally:
        nube.delete_prompt(prompt_id)
