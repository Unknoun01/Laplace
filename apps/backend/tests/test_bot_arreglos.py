"""El bot de PR, más allá del cambio de modelo: `cache_control` y un tope de vueltas.

Son cambios de más de una línea, y por eso se hacen sobre el árbol de sintaxis y no con
una búsqueda de texto: se localiza la llamada (o la función) que anotó el SDK, se cambia
sólo eso, y el fichero resultante tiene que seguir compilando. Lo que no se puede hacer
con seguridad no se propone, y se dice por qué (D-190).
"""

from __future__ import annotations

import ast
import difflib

import pytest

from laplace_backend import arreglos_codigo as ac
from laplace_backend.github_bot import SinPropuesta

AGENTE_CLAUDE = '''import anthropic

cliente = anthropic.Anthropic()

SISTEMA = """Eres el asistente de reservas.
Estas son las normas de la casa: …"""


def responder(pregunta):
    respuesta = cliente.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=400,
        system=SISTEMA,
        messages=[{"role": "user", "content": pregunta}],
    )
    return respuesta.content[0].text


def resumir(texto):
    return cliente.messages.create(model="claude-haiku-4-5", max_tokens=100, messages=[])
'''

LINEA_RESPONDER = 10  # `respuesta = cliente.messages.create(`


def _cambiadas(antes: str, despues: str) -> set[int]:
    """Las líneas del original que el cambio toca (numeradas desde 1)."""
    tocadas: set[int] = set()
    matcher = difflib.SequenceMatcher(None, antes.splitlines(), despues.splitlines())
    for op, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if op != "equal":
            tocadas.update(range(i1 + 1, max(i2, i1 + 1) + 1))
    return tocadas


# ---------------------------------------------------------------------------------
# cache_control
# ---------------------------------------------------------------------------------


def test_el_system_de_la_llamada_pasa_a_un_bloque_con_cache_control():
    nuevo = ac.poner_cache(AGENTE_CLAUDE, LINEA_RESPONDER)
    ast.parse(nuevo)
    assert (
        'system=[{"type": "text", "text": SISTEMA, "cache_control": {"type": "ephemeral"}}],'
        in nuevo
    )
    # Sólo la línea del `system`: ni la constante, ni la otra llamada.
    assert _cambiadas(AGENTE_CLAUDE, nuevo) == {13}


def test_un_system_escrito_en_la_llamada_tambien():
    fuente = AGENTE_CLAUDE.replace("system=SISTEMA,", 'system="Eres breve.",')
    nuevo = ac.poner_cache(fuente, LINEA_RESPONDER)
    assert '"text": "Eres breve.", "cache_control": {"type": "ephemeral"}' in nuevo


def test_un_system_en_varias_lineas_se_cambia_entero_y_compila():
    fuente = AGENTE_CLAUDE.replace(
        "system=SISTEMA,", 'system=(\n            "Eres breve. "\n            + SISTEMA\n        ),'
    )
    nuevo = ac.poner_cache(fuente, LINEA_RESPONDER)
    ast.parse(nuevo)
    llamada = next(
        n for n in ast.walk(ast.parse(nuevo))
        if isinstance(n, ast.Call) and n.lineno == LINEA_RESPONDER
    )
    system = next(k for k in llamada.keywords if k.arg == "system")
    assert isinstance(system.value, ast.List)


def test_una_lista_de_bloques_lleva_el_cache_control_en_el_ultimo():
    fuente = AGENTE_CLAUDE.replace(
        "system=SISTEMA,",
        'system=[{"type": "text", "text": "A"}, {"type": "text", "text": SISTEMA}],',
    )
    nuevo = ac.poner_cache(fuente, LINEA_RESPONDER)
    assert (
        '{"type": "text", "text": "A"}, {"type": "text", "text": SISTEMA, '
        '"cache_control": {"type": "ephemeral"}}' in nuevo
    )


def test_el_prompt_gestionado_de_laplace_es_texto_y_se_envuelve():
    fuente = AGENTE_CLAUDE.replace(
        "system=SISTEMA,", 'system=laplace.get_prompt("reservas").render(hotel=h),'
    )
    nuevo = ac.poner_cache(fuente, LINEA_RESPONDER)
    assert '"text": laplace.get_prompt("reservas").render(hotel=h), "cache_control"' in nuevo


def test_si_ya_cachea_no_hay_nada_que_proponer():
    fuente = AGENTE_CLAUDE.replace(
        "system=SISTEMA,",
        'system=[{"type": "text", "text": SISTEMA, "cache_control": {"type": "ephemeral"}}],',
    )
    with pytest.raises(SinPropuesta) as exc:
        ac.poner_cache(fuente, LINEA_RESPONDER)
    assert exc.value.clave == "pr.ya_cachea"


def test_sin_system_no_se_adivina_que_cachear():
    with pytest.raises(SinPropuesta) as exc:
        ac.poner_cache(AGENTE_CLAUDE, 20)  # la llamada de `resumir`
    assert exc.value.clave == "pr.sin_system"


@pytest.mark.parametrize("valor", ["construir_sistema()", "config.sistema", "partes"])
def test_un_system_que_no_se_sabe_si_es_texto_no_se_toca(valor):
    """Si fuese ya una lista de bloques, envolverla rompería la llamada."""
    fuente = AGENTE_CLAUDE.replace("system=SISTEMA,", f"system={valor},")
    with pytest.raises(SinPropuesta) as exc:
        ac.poner_cache(fuente, LINEA_RESPONDER)
    assert exc.value.clave == "pr.system_desconocido"


def test_una_variable_que_es_una_lista_se_cambia_donde_se_define():
    fuente = AGENTE_CLAUDE.replace(
        '''SISTEMA = """Eres el asistente de reservas.
Estas son las normas de la casa: …"""''',
        'SISTEMA = [{"type": "text", "text": "Normas de la casa"}]',
    )
    nuevo = ac.poner_cache(fuente, LINEA_RESPONDER)
    assert (
        'SISTEMA = [{"type": "text", "text": "Normas de la casa", '
        '"cache_control": {"type": "ephemeral"}}]' in nuevo
    )
    assert "system=SISTEMA," in nuevo


def test_con_tildes_antes_en_la_misma_linea_se_corta_donde_toca():
    """`ast` da las columnas en bytes: con «ñ» antes, cortar por la columna tal cual
    se comería caracteres."""
    fuente = (
        "import anthropic\n"
        "c = anthropic.Anthropic()\n"
        'r = c.messages.create(model="claude-año", system="Señor", messages=[])\n'
    )
    nuevo = ac.poner_cache(fuente, 3)
    assert (
        'model="claude-año", system=[{"type": "text", "text": "Señor", '
        '"cache_control": {"type": "ephemeral"}}], messages=[])' in nuevo
    )


def test_una_linea_fuera_de_cualquier_llamada():
    with pytest.raises(SinPropuesta) as exc:
        ac.poner_cache(AGENTE_CLAUDE, 3)
    assert exc.value.clave == "pr.sin_llamada"


# ---------------------------------------------------------------------------------
# Tope de vueltas
# ---------------------------------------------------------------------------------

AGENTE_BUCLE = '''from laplace import observe

from .herramientas import buscar


@observe(type="agent")
def atender(pregunta):
    while True:
        resultado = buscar(pregunta)
        if resultado:
            return resultado
'''


def test_la_funcion_raiz_lleva_un_guard_con_tope_de_vueltas():
    nuevo = ac.poner_tope(AGENTE_BUCLE, 6, 4)
    ast.parse(nuevo)
    assert "@laplace.guard(max_loop=4)\n@observe(type=\"agent\")\ndef atender" in nuevo
    # `laplace` no estaba importado como módulo: se importa, junto a los otros imports.
    assert nuevo.startswith("import laplace\nfrom laplace import observe\n")


def test_la_linea_puede_ser_la_del_def_o_la_del_decorador():
    assert ac.poner_tope(AGENTE_BUCLE, 7, 4) == ac.poner_tope(AGENTE_BUCLE, 6, 4)


def test_si_laplace_ya_esta_importado_no_se_repite():
    fuente = "import laplace\n" + AGENTE_BUCLE.replace("@observe", "@laplace.observe")
    nuevo = ac.poner_tope(fuente, 7, 4)
    assert nuevo.count("import laplace") == 1


def test_un_metodo_conserva_su_sangria():
    fuente = '''import laplace


class Agente:
    @laplace.observe
    async def atender(self, pregunta):
        return pregunta
'''
    nuevo = ac.poner_tope(fuente, 5, 4)
    ast.parse(nuevo)
    assert "    @laplace.guard(max_loop=4)\n    @laplace.observe\n    async def" in nuevo


def test_si_ya_tiene_guard_no_se_propone_otro():
    fuente = AGENTE_BUCLE.replace("@observe", "@laplace.guard(max_usd_per_run=1)\n@observe")
    with pytest.raises(SinPropuesta) as exc:
        ac.poner_tope("import laplace\n" + fuente, 8, 4)
    assert exc.value.clave == "pr.ya_tiene_tope"


def test_un_generador_no_se_decora():
    """`guard` como decorador cierra el bloque al devolver el generador, antes de que
    corra nada: el tope no protegería nada."""
    fuente = AGENTE_BUCLE.replace("return resultado", "yield resultado")
    with pytest.raises(SinPropuesta) as exc:
        ac.poner_tope(fuente, 6, 4)
    assert exc.value.clave == "pr.tope_generador"


def test_una_linea_fuera_de_una_funcion():
    with pytest.raises(SinPropuesta) as exc:
        ac.poner_tope(AGENTE_BUCLE, 3, 4)
    assert exc.value.clave == "pr.sin_funcion"


def test_lo_que_no_es_python_no_se_toca():
    with pytest.raises(SinPropuesta) as exc:
        ac.poner_tope("export function atender() {}\n", 1, 4)
    assert exc.value.clave == "pr.solo_python"


# ---------------------------------------------------------------------------------
# Dónde está la función: lo anota el SDK en cada paso de @observe
# ---------------------------------------------------------------------------------


def test_un_paso_de_observe_anota_donde_esta_su_funcion_y_llega_por_la_ingesta():
    """Para poner el tope hace falta la función raíz, no la llamada al modelo."""
    import inspect

    from helpers import ingest
    from laplace import observe

    from laplace_backend import github_bot as gb

    @observe(type="agent")
    def atender(pregunta: str) -> str:
        return pregunta

    atender("hola")
    raiz = next(s for s in ingest() if s.name == "atender")
    assert raiz.attributes[gb.ATTR_RUTA] == "apps/backend/tests/test_bot_arreglos.py"
    assert raiz.attributes[gb.ATTR_LINEA] == atender.__wrapped__.__code__.co_firstlineno
    assert raiz.attributes["code.function.name"] == "atender"
    # Y la línea anotada sirve para encontrar la función en el fichero de verdad.
    fuente = inspect.getsource(inspect.getmodule(atender))
    funcion = ac._funcion(ast.parse(fuente), raiz.attributes[gb.ATTR_LINEA])
    assert funcion.name == "atender"


def test_el_sitio_del_cambio_de_modelo_sale_solo_de_las_llamadas_al_modelo():
    """Con los pasos anotados, `sitio` no puede quedarse con la línea de una función."""
    from datetime import datetime, timezone

    from laplace.schema import LLMAttributes, Span

    from laplace_backend import github_bot as gb

    ahora = datetime.now(timezone.utc)

    def span(tipo: str, ruta: str, linea: int) -> Span:
        return Span(
            span_id=f"{tipo}{linea}", trace_id="t", project_id="p", name=tipo, type=tipo,
            start_time=ahora, end_time=ahora,
            llm=LLMAttributes(request_model="gpt-5.5") if tipo == "llm" else None,
            attributes={gb.ATTR_RUTA: ruta, gb.ATTR_LINEA: linea},
        )

    pasos = [span("agent", "agente.py", 3), span("chain", "agente.py", 3),
             span("tool", "agente.py", 3), span("llm", "agente.py", 9)]
    assert gb.sitio(pasos, "gpt-5.5") == ("agente.py", 9)


# ---------------------------------------------------------------------------------
# De la ficha al PR, por la API
# ---------------------------------------------------------------------------------

AGENTE_BUCLE_REPO = AGENTE_BUCLE.replace("from .herramientas import buscar", "from x import buscar")


def _sembrar_contexto(store, project: str, modelo: str = "claude-sonnet-4-5") -> None:
    """Cuatro ejecuciones de tres llamadas que reenvían 3.000 tokens fijos sin caché."""
    import uuid
    from datetime import datetime, timedelta, timezone

    from laplace.schema import Cost, LLMAttributes, Span, TokenUsage

    from laplace_backend import github_bot as gb

    ahora = datetime.now(timezone.utc)
    spans = []
    trazas = [uuid.uuid4().hex for _ in range(4)]
    for n in range(12):
        traza = trazas[n // 3]
        inicio = ahora - timedelta(hours=2, minutes=n)
        raiz = f"r{n // 3:03d}".ljust(16, "0")
        if n % 3 == 0:
            spans.append(Span(span_id=raiz, trace_id=traza, project_id=project,
                              name="agente", type="agent", status="ok", start_time=inicio,
                              end_time=inicio + timedelta(seconds=2), duration_ms=2000))
        spans.append(Span(
            span_id=f"l{n:03d}".ljust(16, "0"), parent_span_id=raiz, trace_id=traza,
            project_id=project, name="responder", type="llm", status="ok",
            start_time=inicio, end_time=inicio + timedelta(seconds=1), duration_ms=1000,
            step_key="k_responder", step_label="responder", dedup_hash=f"h{n}",
            llm=LLMAttributes(
                system="anthropic", request_model=modelo, response_model=modelo,
                usage=TokenUsage(input_tokens=3000 + n, output_tokens=200),
                cost=Cost(input_usd=0.009, output_usd=0.003, total_usd=0.012),
            ),
            attributes={gb.ATTR_RUTA: "agente/reservas.py", gb.ATTR_LINEA: LINEA_RESPONDER},
        ))
    store.insert_spans(spans)


def _sembrar_bucle(store, project: str, *, con_sitio: bool = True) -> None:
    """Tres ejecuciones en las que `buscar` da seis vueltas con la misma salida."""
    import uuid
    from datetime import datetime, timedelta, timezone

    from laplace.schema import Cost, LLMAttributes, Span, TokenUsage

    from laplace_backend import github_bot as gb

    ahora = datetime.now(timezone.utc)
    spans = []
    for n in range(3):
        traza = uuid.uuid4().hex
        inicio = ahora - timedelta(hours=1, minutes=n)
        raiz = f"a{n:03d}".ljust(16, "0")
        spans.append(Span(
            span_id=raiz, trace_id=traza, project_id=project, name="atender", type="agent",
            status="ok", start_time=inicio, end_time=inicio + timedelta(seconds=9),
            duration_ms=9000,
            attributes={gb.ATTR_RUTA: "agente/bucle.py", gb.ATTR_LINEA: 6} if con_sitio else {},
        ))
        for v in range(6):
            spans.append(Span(
                span_id=f"b{n}{v:02d}".ljust(16, "0"), parent_span_id=raiz, trace_id=traza,
                project_id=project, name="buscar", type="llm", status="ok",
                start_time=inicio + timedelta(seconds=v),
                end_time=inicio + timedelta(seconds=v + 1),
                duration_ms=1000, step_key="k_buscar", step_label="buscar",
                dedup_hash=f"d{n}{v}", loop_hash="L1", loop_out_hash="O1",
                llm=LLMAttributes(
                    system="openai", request_model="gpt-5.5", response_model="gpt-5.5",
                    usage=TokenUsage(input_tokens=300, output_tokens=20),
                    cost=Cost(input_usd=0.0009, output_usd=0.0001, total_usd=0.001),
                ),
            ))
    store.insert_spans(spans)


@pytest.fixture
def api(tmp_path, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c, main.app
    config.get_settings.cache_clear()


def _conectar(c, monkeypatch, ficheros: dict[str, str]):
    from test_github_bot import GitHubFalso

    from laplace_backend import github_bot as gb

    github = GitHubFalso(ficheros)
    monkeypatch.setattr(gb, "_pedir", github)
    r = c.put("/api/github", json={"project_id": "p", "repo": "acme/agentes",
                                   "token": "tok-usuario"})
    assert r.status_code == 200, r.text
    return github


def _hallazgo(c, tipo: str) -> dict:
    hallazgos = c.get("/api/overview", params={"project_id": "p", "days": 7}).json()["findings"]
    return next(h for h in hallazgos if h["kind"] == tipo)


def test_del_contexto_fijo_al_pr_con_cache_control(api, monkeypatch):
    from laplace_backend import github_bot as gb

    c, app = api
    _sembrar_contexto(app.state.store, "p")
    github = _conectar(c, monkeypatch, {"servicios/agente/reservas.py": AGENTE_CLAUDE})
    hallazgo = _hallazgo(c, "contexto_fijo")
    assert hallazgo["code_fix"] == "cache"

    r = c.post("/api/github/pull-request", json={"project_id": "p", "finding_id": hallazgo["id"]})
    assert r.status_code == 200, r.text
    nuevo = github.fichero(gb.rama_para(hallazgo["id"]), "servicios/agente/reservas.py")
    assert nuevo == ac.poner_cache(AGENTE_CLAUDE, LINEA_RESPONDER)
    assert "cache_control" in github.prs[0]["title"]


def test_con_openai_el_contexto_fijo_no_ofrece_pr(api):
    """OpenAI cachea solo: no hay `cache_control` que escribir."""
    c, app = api
    _sembrar_contexto(app.state.store, "p", modelo="gpt-5.5")
    hallazgo = _hallazgo(c, "contexto_fijo")
    assert hallazgo["code_fix"] == ""
    r = c.post("/api/github/pull-request", json={"project_id": "p", "finding_id": hallazgo["id"]})
    assert r.status_code == 409


def test_del_bucle_al_pr_con_un_tope_en_la_funcion_raiz(api, monkeypatch):
    from laplace_backend import github_bot as gb
    from laplace_backend.insights.modelos import MIN_VUELTAS_BUCLE

    c, app = api
    _sembrar_bucle(app.state.store, "p")
    github = _conectar(c, monkeypatch, {"agente/bucle.py": AGENTE_BUCLE_REPO})
    hallazgo = _hallazgo(c, "bucle")
    assert hallazgo["code_fix"] == "tope"

    r = c.post("/api/github/pull-request", json={"project_id": "p", "finding_id": hallazgo["id"]})
    assert r.status_code == 200, r.text
    nuevo = github.fichero(gb.rama_para(hallazgo["id"]), "agente/bucle.py")
    assert nuevo == ac.poner_tope(AGENTE_BUCLE_REPO, 6, MIN_VUELTAS_BUCLE)
    assert f"max_loop={MIN_VUELTAS_BUCLE}" in github.prs[0]["body"]


def test_un_bucle_sin_la_funcion_anotada_dice_por_que(api, monkeypatch):
    c, app = api
    _sembrar_bucle(app.state.store, "p", con_sitio=False)
    _conectar(c, monkeypatch, {"agente/bucle.py": AGENTE_BUCLE_REPO})
    hallazgo = _hallazgo(c, "bucle")
    r = c.post("/api/github/pull-request", json={"project_id": "p", "finding_id": hallazgo["id"]})
    assert r.status_code == 409
    assert "SDK" in r.json()["detail"]


def test_un_cambio_que_no_compila_no_se_propone(api, monkeypatch):
    """La comprobación final vale para todos los arreglos, también el de modelo."""
    from laplace_backend import github_bot as gb

    def _roto(fuente: str, ruta: str) -> str:
        return fuente + "\ndef (:\n"

    with pytest.raises(SinPropuesta) as exc:
        from test_github_bot import AJUSTES, GitHubFalso

        gb.abrir(AJUSTES, "x", ruta_anotada="agente/bucle.py", cambiar=_roto,
                 titulo="t", cuerpo="c", mensaje="m",
                 pedir=GitHubFalso({"agente/bucle.py": AGENTE_BUCLE_REPO}))
    assert exc.value.clave == "pr.no_compila"
