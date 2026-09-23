"""Cobertura: que el silencio del producto no se lea como una buena noticia.

Es el peor fallo que puede tener Laplace porque es **indistinguible del éxito**. Cuando
la identidad de un paso se parte, las reglas se callan por falta de llamadas, la pantalla
dice «no estás tirando dinero ahora mismo» y eso se lee como que todo va bien. Lo que de
verdad ha pasado es que no entendemos el agente.

Lo que se comprueba aquí:

1. Que las cuatro señales salen de las trazas y **dicen lo mismo en los dos almacenes**.
2. Que un paso partido sube el nivel aunque los cuatro porcentajes estén al 100 %: es el
   caso que se cuela por debajo de cualquier media.
3. Que sin llamadas suficientes no hay porcentaje, sino `None` y el motivo.
4. Que no gestionar prompts no cuenta como un defecto.
5. Que «no hay nada que arreglar» deja de ser verde cuando no hemos podido mirar.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage

from laplace_backend import coverage as cov
from laplace_backend.storage.base import CoverageFacts, Window
from laplace_backend.storage.sqlite import SQLiteStore

#: Relativo a ahora y no una fecha fija. Con una fecha fija, el test que pasa por la
#: API —que pide los últimos 7 días— se ponía en rojo solo al cabar una semana desde
#: la fecha escrita, y el fallo no tenía nada que ver con lo que el test comprueba.
AHORA = datetime.now(timezone.utc) - timedelta(minutes=30)


def _facts(**cambios) -> CoverageFacts:
    base = dict(
        llm_calls=100,
        identified_steps=100,
        priced=100,
        measured_tokens=100,
        with_prompt_version=100,
        steps=3,
        split_steps=[],
    )
    base.update(cambios)
    return CoverageFacts(**base)


def _señal(cobertura: cov.Coverage, clave: str) -> cov.Signal:
    return next(s for s in cobertura.signals if s.key == clave)


# ---------------------------------------------------------------------------------
# La guarda de siempre
# ---------------------------------------------------------------------------------


def test_con_pocas_llamadas_no_hay_porcentaje():
    """Quinta vez que aparece el mismo error: un porcentaje sobre cuatro casos."""
    cobertura = cov.build(_facts(llm_calls=4, identified_steps=2, priced=4, measured_tokens=4,
                                 with_prompt_version=0))
    pasos = _señal(cobertura, "pasos")
    assert pasos.value is None
    assert "4 llamadas" in pasos.unavailable
    assert cobertura.level == "sin-base"
    assert cobertura.prominent is False
    assert "todavía no podemos decir" in cobertura.headline.lower()


def test_sin_llamadas_no_se_inventa_nada():
    cobertura = cov.build(_facts(llm_calls=0, identified_steps=0, priced=0,
                                 measured_tokens=0, with_prompt_version=0, steps=0))
    assert all(s.value is None for s in cobertura.signals)
    assert cobertura.level == "sin-base"


# ---------------------------------------------------------------------------------
# El veredicto
# ---------------------------------------------------------------------------------


def test_todo_alto_deja_el_aviso_en_una_linea():
    cobertura = cov.build(_facts())
    assert cobertura.level == "bien"
    assert cobertura.prominent is False, "no puede robarle el sitio al dinero si va bien"
    assert "entendemos" in cobertura.headline.lower()


def test_una_senal_mala_manda_sobre_las_otras_tres_buenas():
    """Promediar las cuatro escondería justo el caso que importa: tres perfectas y la de
    los pasos por los suelos dan una media tranquilizadora y un diagnóstico inútil."""
    cobertura = cov.build(_facts(identified_steps=55))
    assert cobertura.level == "malo"
    assert cobertura.prominent is True
    assert "se nos escapan" in cobertura.headline
    # Y dice qué hacer, no sólo que está mal: en la señal que va mal, que es donde la
    # pantalla lo pinta.
    assert "@laplace.observe" in _señal(cobertura, "pasos").fix
    # Pero una sola vez. Repetirlo en el texto del bloque era leer el mismo párrafo dos
    # veces seguidas (D-122).
    assert "@laplace.observe" not in cobertura.detail


def test_la_consecuencia_dice_que_el_silencio_no_es_una_buena_noticia():
    cobertura = cov.build(_facts(identified_steps=50))
    pasos = _señal(cobertura, "pasos")
    assert "«no lo sabemos», no «está bien»" in pasos.consequence


def test_un_paso_partido_sube_el_nivel_aunque_todo_este_al_cien():
    """El caso exacto que esta sección existe para destapar: cada llamada tiene
    identidad, pero una **distinta**, así que el paso no se agrupa y nadie lo mira."""
    cobertura = cov.build(_facts(split_steps=["resumir"]))
    assert all(
        s.value == 1.0 for s in cobertura.signals if s.value is not None
    ), "los cuatro porcentajes están perfectos"
    assert cobertura.level == "flojo"
    assert cobertura.prominent is True
    assert "«resumir»" in cobertura.headline
    assert "no lo hemos mirado" in cobertura.detail


def test_un_paso_partido_no_dispara_con_dos_llamadas():
    """Sin base no se afirma, tampoco esto."""
    cobertura = cov.build(_facts(llm_calls=4, split_steps=["resumir"]))
    assert cobertura.prominent is False


def test_no_gestionar_prompts_no_es_un_defecto():
    """Cero versiones de prompt es lo normal para quien no ha adoptado esa parte. Si
    contara como fallo, todo el mundo vería rojo el primer día."""
    cobertura = cov.build(_facts(with_prompt_version=0), has_managed_prompts=False)
    prompts = _señal(cobertura, "prompts")
    assert prompts.level == "no-aplica"
    assert cobertura.level == "bien"
    assert cobertura.prominent is False


def test_gestionar_prompts_y_no_marcarlos_si_es_un_defecto():
    """Si el proyecto tiene prompts gestionados y las trazas no los llevan, algo está
    mal de verdad: o el SDK es viejo o el texto se está reescribiendo antes de mandarlo."""
    cobertura = cov.build(_facts(with_prompt_version=10), has_managed_prompts=True)
    prompts = _señal(cobertura, "prompts")
    assert prompts.level == "malo"
    assert cobertura.prominent is True


# ---------------------------------------------------------------------------------
# Contra los almacenes de verdad
# ---------------------------------------------------------------------------------


def _span(project: str, trace: str, *, hint: str, label: str, name: str,
          key: str, unknown: bool = False, estimated: bool = False,
          prompt: str = "", version: int = 0, tokens: int = 500, site: str = "") -> Span:
    span = Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace,
        project_id=project,
        name=name,
        type="llm",
        status="ok",
        start_time=AHORA,
        end_time=AHORA + timedelta(milliseconds=300),
        duration_ms=300.0,
        step_key=key,
        step_label=label,
        step_hint=hint,
        step_site=site,
        prompt_name=prompt,
        prompt_version=version,
    )
    span.llm = LLMAttributes(
        request_model="gpt-5.6-luna",
        usage=TokenUsage(input_tokens=tokens, output_tokens=20, estimated=estimated),
        cost=Cost(total_usd=0.0 if unknown else 0.001, unknown=unknown),
    )
    return span


def _sembrar(store, project: str) -> None:
    spans = []
    # 10 llamadas sanas: sitio de llamada distinto del nombre del span.
    for i in range(10):
        spans.append(
            _span(project, f"{project}-ok-{i}", hint="Eres un asistente.",
                  label="responder", name="chat gpt-5.6-luna", key="k-ok",
                  prompt="atencion", version=3)
        )
    # 4 sin identidad: cayeron al nombre del span (ni sitio ni instrucciones).
    for i in range(4):
        spans.append(
            _span(project, f"{project}-mudo-{i}", hint="", label="chat gpt-5.6-luna",
                  name="chat gpt-5.6-luna", key="k-mudo")
        )
    # 3 sin tarifa y 2 con tokens estimados.
    for i in range(3):
        spans.append(
            _span(project, f"{project}-sin-tarifa-{i}", hint="Eres un asistente.",
                  label="responder", name="chat raro", key="k-ok", unknown=True)
        )
    for i in range(2):
        spans.append(
            _span(project, f"{project}-estimado-{i}", hint="Eres un asistente.",
                  label="responder", name="chat gpt-5.6-luna", key="k-ok", estimated=True)
        )
    # 6 de un paso partido: una huella por ejecución.
    for i in range(6):
        spans.append(
            _span(project, f"{project}-partido-{i}", hint=f"Hoy es {i} de marzo.",
                  label="resumir", name="chat gpt-5.6-luna", key=f"k-var-{i}")
        )
    store.insert_spans(spans)


def _ventana() -> Window:
    return Window(since=AHORA - timedelta(days=1), until=AHORA + timedelta(days=1), days=2)


def test_las_senales_salen_de_las_trazas(tmp_path):
    store = SQLiteStore(tmp_path / "c.db")
    store.migrate()
    _sembrar(store, "p")
    facts = store.coverage("p", _ventana())

    assert facts.llm_calls == 25
    # Identificadas: todas menos las 4 que cayeron al nombre del span.
    assert facts.identified_steps == 21
    assert facts.priced == 22
    assert facts.measured_tokens == 23
    assert facts.with_prompt_version == 10
    # Y el paso partido aparece con su nombre, no como un número.
    assert facts.split_steps == ["resumir"]


def test_los_dos_almacenes_cuentan_la_cobertura_igual(tmp_path):
    """Si divergieran, el mismo proyecto se vería sano en local y roto en la nube —o al
    revés—, que es la peor forma posible de estropear justo esta pantalla."""
    from laplace_backend.config import Settings
    from laplace_backend.storage.clickhouse import ClickHouseStore

    nube = ClickHouseStore(Settings())
    if not nube.health():
        pytest.skip("no hay ClickHouse escuchando; no se puede comparar")
    nube.migrate()

    project = f"cobertura-{uuid.uuid4().hex[:8]}"
    local = SQLiteStore(tmp_path / "c.db")
    local.migrate()
    _sembrar(local, project)
    _sembrar(nube, project)
    try:
        aqui = local.coverage(project, _ventana())
        alli = nube.coverage(project, _ventana())
        assert aqui == alli
    finally:
        nube.delete_project(project)


def test_el_inicio_trae_la_cobertura_delante(tmp_path, monkeypatch):
    """Que llegue hasta la API: es lo que decide si el usuario lo ve antes o después de
    la cifra de ahorro."""
    import importlib

    from fastapi.testclient import TestClient

    db = tmp_path / "laplace.db"
    store = SQLiteStore(db)
    store.migrate()
    _sembrar(store, "local")

    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(db))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as client:
        cuerpo = client.get("/api/overview", params={"project_id": "local", "days": 7}).json()
    config.get_settings.cache_clear()

    cobertura = cuerpo["coverage"]
    assert cobertura is not None
    assert cobertura["llm_calls"] == 25
    assert cobertura["prominent"] is True, "con 4 de 25 llamadas mudas hay que avisar"
    assert cobertura["split_steps"] == ["resumir"]
    # Y la señal de prompts no se lee como defecto: este proyecto no gestiona prompts.
    prompts = next(s for s in cobertura["signals"] if s["key"] == "prompts")
    assert prompts["level"] == "no-aplica"


# ---------------------------------------------------------------------------------
# Dos llamantes con el mismo nombre: el sano no puede tapar al roto
# ---------------------------------------------------------------------------------


def _dos_llamantes(store, project: str, *, con_camino: bool) -> None:
    """Dos agentes con una función homónima: `resumir` en los dos.

    En el agente A las instrucciones son fijas —una identidad para todas sus
    ejecuciones—. En el B llevan la fecha dentro, así que cada ejecución estrena
    identidad: el paso está partido y hay que decirlo.

    `con_camino=False` reproduce lo que guardaban las trazas antes de D-106, cuando el
    sitio era sólo el nombre de la función.
    """
    spans = []
    for i in range(12):
        spans.append(
            _span(project, f"{project}-sano-{i}", hint="Resume el ticket.", label="resumir",
                  name="chat gpt-5.6-luna", key="sano-una-sola",
                  site="atender_bien > resumir" if con_camino else "")
        )
    for i in range(12):
        spans.append(
            _span(project, f"{project}-roto-{i}", hint=f"Hoy es 2026-09-21 12:00:{i:02d}.",
                  label="resumir", name="chat gpt-5.6-luna", key=f"roto-{i}",
                  site="atender_mal > resumir" if con_camino else "")
        )
    store.insert_spans(spans)


def test_un_llamante_sano_no_puede_tapar_a_uno_roto_con_el_mismo_nombre(tmp_path):
    """El fallo de D-106, con su prueba.

    Dos agentes llaman a una función que en los dos se llama `resumir`. El de uno está
    partido —una identidad por ejecución— y el del otro no. Agrupando por nombre, las 12
    ejecuciones limpias diluyen a las 12 rotas: 13 identidades sobre 24 trazas es un 0,54
    y no llega al umbral de 0,8, así que el producto se calla **justo cuando hay algo
    roto**. Es la media que esconde el caso que importa, otra vez.

    Agrupando por sitio de llamada, el roto se mide solo: 12 identidades sobre 12
    ejecuciones, y salta.
    """
    store = SQLiteStore(tmp_path / "c.db")
    store.migrate()
    _dos_llamantes(store, "p", con_camino=True)

    facts = store.coverage("p", _ventana())
    assert facts.llm_calls == 24
    assert facts.split_steps == ["resumir"], "el roto tiene que salir, aunque el otro esté sano"

    cobertura = cov.build(facts)
    assert cobertura.prominent is True
    assert "«resumir»" in cobertura.headline


def test_sin_camino_de_llamada_el_sano_tapaba_al_roto(tmp_path):
    """La prueba de la prueba: con lo que guardaban las trazas antes de D-106, el mismo
    tráfico no dispara. Si algún día alguien «simplifica» la agrupación y vuelve al
    nombre, el test de arriba se pondría en rojo y éste seguiría en verde: los dos
    juntos dicen qué cambió y por qué.
    """
    store = SQLiteStore(tmp_path / "c.db")
    store.migrate()
    _dos_llamantes(store, "p", con_camino=False)

    facts = store.coverage("p", _ventana())
    assert facts.llm_calls == 24
    assert facts.split_steps == [], "sin camino, los dos llamantes caen en el mismo montón"
