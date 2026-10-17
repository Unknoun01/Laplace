"""Los arreglos de texto (D-221): los escribe un modelo, los prueba un replay y los
aprueba una persona.

Con un proveedor falso y un GitHub falso se exige:

* que el texto se localice (literal en el código o prompt versionado) y que sea el que
  grabó la traza; si no, no hay propuesta;
* que lo que escribe el modelo se compruebe (objetivo, variables) y, en el código, sea
  sólo el valor de una constante: el resto del árbol de sintaxis, igual;
* que lo que cuesta proponer se apunte, también lo rechazado;
* que sin replay de esa misma propuesta no se apruebe, y que aprobar sea de una persona
  y de lo que se vio;
* que un prompt versionado acabe en una versión nueva, sin desplegar.
"""

from __future__ import annotations

import ast
import importlib
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage

from laplace_backend import arreglos_texto as at
from laplace_backend import github_bot as gb
from laplace_backend import judge
from laplace_backend.github_bot import SinPropuesta

sys.path.insert(0, str(Path(__file__).parent))
from test_bot_arreglos import AGENTE_CLAUDE, LINEA_RESPONDER  # noqa: E402

SISTEMA = "Eres el asistente de reservas.\nEstas son las normas de la casa: …"
CORTO = "Asistente de reservas: aplica las normas."
MODELO = "claude-sonnet-4-5"

AGENTE_OPENAI = '''from openai import OpenAI

cliente = OpenAI()


def responder(pregunta):
    return cliente.chat.completions.create(
        model="gpt-5.5",
        messages=[
            {"role": "system", "content": "Eres el asistente de reservas."},
            {"role": "user", "content": pregunta},
        ],
    )
'''


# ---------------------------------------------------------------------------------
# Comprobar lo que escribe el modelo
# ---------------------------------------------------------------------------------


def test_acortar_exige_un_texto_bastante_mas_corto():
    assert at.validar(SISTEMA, CORTO, "acortar") == ""
    assert at.validar(SISTEMA, SISTEMA[:-2], "acortar") == "texto.no_acorta"
    assert at.validar(SISTEMA, "   ", "acortar") == "texto.vacio"


def test_json_no_puede_crecer_sin_medida():
    original = "Devuelve la respuesta en JSON."
    assert at.validar(original, "Devuelve sólo JSON válido, sin texto alrededor.", "json") == ""
    assert at.validar(original, "x" * 61, "json") == "texto.crece"


def test_las_variables_de_la_plantilla_se_conservan():
    original = "Hola {{ cliente }}. Normas: muchas, largas y repetidas, de {{hotel}}."
    assert at.validar(original, "Hola {{cliente}} de {{ hotel }}.", "acortar") == ""
    assert at.validar(original, "Hola {{ cliente }}.", "acortar") == "texto.variables"
    assert at.validar(original, "{{ cliente }} {{ hotel }} {{ x }}", "acortar") == (
        "texto.variables"
    )


def test_el_texto_viejo_se_cambia_en_lo_grabado_tambien_con_variables():
    grabado = "Antes. Hola Ana, bienvenida a Sol. Después."
    nuevo = at.cambiar_en_texto(grabado, "Hola {{ cliente }}, bienvenida a {{hotel}}.",
                                "{{hotel}}: hola {{cliente}}.")
    assert nuevo == "Antes. Sol: hola Ana. Después."
    assert at.cambiar_en_texto("otra cosa", "Hola {{ cliente }}.", "x") is None
    assert at.cambiar_en_texto("a. Sin variables. b", "Sin variables.", "Corto.") == "a. Corto. b"


def test_en_el_replay_solo_cambian_las_instrucciones_de_sistema():
    from laplace_backend.replay import Llamada, Reenviables

    def llamada(sistema: str) -> Llamada:
        return Llamada(case_id="c", trace_id="t", span_id="s", messages=[
            {"role": "system", "content": sistema},
            {"role": "user", "content": f"Repite esto: {SISTEMA}"},
        ])

    propuesta = {"original": SISTEMA, "propuesto": CORTO, "sha": "abc"}
    r = at.en_reenviables(Reenviables(dataset_id="d", calls=[llamada(SISTEMA), llamada("otro")]),
                          propuesta)
    assert r.proposal == "abc" and r.excluded == {"sin_el_texto": 1}
    assert r.calls[0].messages == [{"role": "system", "content": CORTO},
                                   {"role": "user", "content": f"Repite esto: {SISTEMA}"}]


# ---------------------------------------------------------------------------------
# En el código: sólo el valor de un literal
# ---------------------------------------------------------------------------------


def test_el_literal_del_system_se_encuentra_tambien_en_una_constante():
    assert at.literal_en(AGENTE_CLAUDE, LINEA_RESPONDER) == SISTEMA
    assert at.literal_en(AGENTE_OPENAI, 7) == "Eres el asistente de reservas."


def test_sustituir_el_literal_deja_el_resto_del_arbol_igual():
    nuevo = at.sustituir_literal(AGENTE_CLAUDE, LINEA_RESPONDER, SISTEMA, CORTO)
    assert at.literal_en(nuevo, LINEA_RESPONDER) == CORTO
    antes, despues = ast.parse(AGENTE_CLAUDE), ast.parse(nuevo)
    for nodo in ast.walk(despues):
        if isinstance(nodo, ast.Constant) and nodo.value == CORTO:
            nodo.value = SISTEMA
    assert ast.dump(antes) == ast.dump(despues)


@pytest.mark.parametrize(
    "malicioso",
    [
        '"""\nimport os; os.system("rm -rf /")\n"""',
        "\\\"); __import__('os').system('x'); (\"",
        "fin con barra \\",
    ],
)
def test_lo_que_escribe_el_modelo_queda_como_texto_y_no_se_ejecuta(malicioso):
    nuevo = at.sustituir_literal(AGENTE_CLAUDE, LINEA_RESPONDER, SISTEMA, malicioso)
    assert at.literal_en(nuevo, LINEA_RESPONDER) == malicioso
    llamadas = [n for n in ast.walk(ast.parse(nuevo)) if isinstance(n, ast.Call)]
    assert len(llamadas) == len([n for n in ast.walk(ast.parse(AGENTE_CLAUDE))
                                 if isinstance(n, ast.Call)])


def test_un_system_que_no_es_un_literal_no_se_toca():
    con_fstring = AGENTE_CLAUDE.replace("system=SISTEMA", 'system=f"{SISTEMA} hoy"')
    with pytest.raises(SinPropuesta) as exc:
        at.literal_en(con_fstring, LINEA_RESPONDER)
    assert exc.value.clave == "texto.no_literal"


def test_si_el_literal_ya_no_dice_lo_que_decia_no_hay_cambio():
    with pytest.raises(SinPropuesta) as exc:
        at.sustituir_literal(AGENTE_CLAUDE, LINEA_RESPONDER, "Otro texto", CORTO)
    assert exc.value.clave == "texto.cambio_el_fichero"


# ---------------------------------------------------------------------------------
# El modelo propone
# ---------------------------------------------------------------------------------


def _config():
    from laplace_backend.diagnostico_modelo import DiagnosisConfig

    return DiagnosisConfig(
        juez=judge.JudgeConfig(enabled=True, model="claude-sonnet-5", api_key="k"),
        enabled=True,
    )


def _proveedor(monkeypatch, respuesta: dict | str, tokens=(900, 60)):
    enviado: dict = {}

    def post(url, payload, headers):
        enviado.update(payload)
        texto = respuesta if isinstance(respuesta, str) else json.dumps(respuesta)
        return {
            "content": [{"type": "text", "text": texto}],
            "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]},
        }

    monkeypatch.setattr(judge, "_post", post)
    return enviado


def test_el_modelo_propone_y_se_mide_lo_que_cuesta(monkeypatch):
    enviado = _proveedor(monkeypatch, {"text": CORTO, "explanation": "Quita lo repetido."})
    texto, explicacion, gasto = at.pedir_texto(_config(), SISTEMA, "acortar", "Contexto fijo")
    assert (texto, explicacion) == (CORTO, "Quita lo repetido.")
    assert gasto["input_tokens"] == 900 and gasto["cost_usd"] > 0 and gasto["aceptada"]
    # El texto actual va como dato, y un cierre de etiqueta dentro no se sale.
    assert "<dato>" in enviado["messages"][0]["content"]


def test_lo_que_no_pasa_la_comprobacion_se_rechaza_con_su_coste(monkeypatch):
    _proveedor(monkeypatch, {"text": SISTEMA + " y más", "explanation": ""})
    with pytest.raises(at.Rechazada) as exc:
        at.pedir_texto(_config(), SISTEMA, "acortar", "Contexto fijo")
    assert exc.value.clave == "texto.no_acorta"
    assert exc.value.gasto["cost_usd"] > 0 and not exc.value.gasto["aceptada"]

    _proveedor(monkeypatch, "no es JSON")
    with pytest.raises(at.Rechazada) as exc:
        at.pedir_texto(_config(), SISTEMA, "acortar", "Contexto fijo")
    assert exc.value.clave == "texto.sin_json"


# ---------------------------------------------------------------------------------
# Por la API
# ---------------------------------------------------------------------------------


def _sembrar(store, project: str, *, prompt: tuple[str, int] | None = None,
             sistema: str = SISTEMA) -> None:
    """Cuatro ejecuciones de tres llamadas que reenvían el mismo contexto fijo sin caché."""
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
                system="anthropic", request_model=MODELO, response_model=MODELO,
                input_messages=[{"role": "system", "content": sistema},
                                {"role": "user", "content": f"pregunta {n}"}],
                output_messages=[{"role": "assistant", "content": f"respuesta {n}"}],
                usage=TokenUsage(input_tokens=3000 + n, output_tokens=200),
                cost=Cost(input_usd=0.009, output_usd=0.003, total_usd=0.012),
            ),
            prompt_name=prompt[0] if prompt else "",
            prompt_version=prompt[1] if prompt else 0,
            attributes={gb.ATTR_RUTA: "agente/reservas.py", gb.ATTR_LINEA: LINEA_RESPONDER},
        ))
    store.insert_spans(spans)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    monkeypatch.setenv("LAPLACE_HOME", str(tmp_path))
    monkeypatch.setenv("LAPLACE_EVALS_JUDGE_API_KEY", "k")
    monkeypatch.setenv("LAPLACE_EVALS_JUDGE_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("LAPLACE_DIAGNOSIS_ENABLED", "true")
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c, main.app
    config.get_settings.cache_clear()


def _conectar(c, monkeypatch, ficheros: dict[str, str]):
    from test_github_bot import GitHubFalso

    github = GitHubFalso(ficheros)
    monkeypatch.setattr(gb, "_pedir", github)
    r = c.put("/api/github", json={"project_id": "p", "repo": "acme/agentes",
                                   "token": "tok-usuario"})
    assert r.status_code == 200, r.text
    return github


def _hallazgo(c) -> dict:
    hallazgos = c.get("/api/overview", params={"project_id": "p", "days": 7}).json()["findings"]
    return next(h for h in hallazgos if h["kind"] == "contexto_fijo")


def _conjunto(c) -> dict:
    r = c.post("/api/datasets", json={"project_id": "p", "name": "responder",
                                      "filter": {"step_key": "k_responder"}, "limit": 10})
    assert r.status_code == 200, r.text
    return r.json()


def _tirada(c, conjunto: dict, sha: str, *, fallida: bool = False) -> None:
    """Las dos tiradas que deja `laplace replay --propuesta` (lo prueba test_replay_sdk)."""
    trazas = c.get("/api/traces", params={"project_id": "p", "limit": 1}).json()["traces"]
    item = {"case_id": "c1", "trace_id": trazas[0]["trace_id"], "failed": fallida}
    for variante in (f"original · propuesta {sha}", f"propuesta {sha}"):
        r = c.post("/api/runs", json={"project_id": "p", "dataset_id": conjunto["id"],
                                      "variant": variante, "items": [item]})
        assert r.status_code == 200, r.text


def _proponer(c, hallazgo):
    return c.post("/api/arreglos-texto/proponer",
                  json={"project_id": "p", "finding_id": hallazgo["id"]})


def test_del_contexto_fijo_al_pr_pasando_por_el_replay(api, monkeypatch):
    c, app = api
    _sembrar(app.state.store, "p")
    github = _conectar(c, monkeypatch, {"servicios/agente/reservas.py": AGENTE_CLAUDE})
    _proveedor(monkeypatch, {"text": CORTO, "explanation": "Quita lo repetido."})
    hallazgo = _hallazgo(c)
    conjunto = _conjunto(c)

    r = _proponer(c, hallazgo)
    assert r.status_code == 200, r.text
    vista = r.json()
    propuesta = vista["propuesta"]
    assert propuesta["propuesto"] == CORTO and propuesta["original"] == SISTEMA
    assert propuesta["donde"] == {"tipo": "codigo", "ruta": "servicios/agente/reservas.py",
                                  "linea": LINEA_RESPONDER}
    assert any(linea["op"] == "+" for linea in vista["diff"]["lines"])
    assert vista["gasto"]["usd"] > 0 and vista["gasto"]["calls"] == 1
    assert vista["probada"] is None and vista["conjunto"] == conjunto["name"]
    sha = propuesta["sha"]

    # Sin replay no se aprueba.
    aprobar = {"project_id": "p", "finding_id": hallazgo["id"], "sha": sha}
    r = c.post("/api/arreglos-texto/aprobar", json=aprobar)
    assert r.status_code == 409, r.text
    assert github.prs == []

    # El replay recibe las llamadas con el texto propuesto.
    r = c.get(f"/api/datasets/{conjunto['id']}/replay",
              params={"model": MODELO, "propuesta": hallazgo["id"]})
    reenviables = r.json()
    assert reenviables["proposal"] == sha
    assert reenviables["calls"][0]["messages"][0] == {"role": "system", "content": CORTO}

    # Un replay en el que todo falló no sostiene nada.
    _tirada(c, conjunto, sha, fallida=True)
    assert c.post("/api/arreglos-texto/aprobar", json=aprobar).status_code == 409

    _tirada(c, conjunto, sha)
    vista = c.get("/api/arreglos-texto",
                  params={"project_id": "p", "finding_id": hallazgo["id"]}).json()
    assert vista["probada"]["casos"] == 1 and vista["probada"]["run_id"]

    # Lo aprobado tiene que ser lo que se vio.
    r = c.post("/api/arreglos-texto/aprobar", json={**aprobar, "sha": "otra"})
    assert r.status_code == 409
    r = c.post("/api/arreglos-texto/aprobar", json=aprobar)
    assert r.status_code == 200, r.text
    nuevo = github.fichero(gb.rama_para("texto:" + hallazgo["id"]),
                           "servicios/agente/reservas.py")
    assert nuevo == at.sustituir_literal(AGENTE_CLAUDE, LINEA_RESPONDER, SISTEMA, CORTO)
    assert github.fichero("main", "servicios/agente/reservas.py") == AGENTE_CLAUDE
    assert r.json()["url"].startswith("https://github.com/acme/agentes/pull/")


def test_una_propuesta_nueva_deja_viejo_el_replay_de_la_anterior(api, monkeypatch):
    c, app = api
    _sembrar(app.state.store, "p")
    _conectar(c, monkeypatch, {"agente/reservas.py": AGENTE_CLAUDE})
    _proveedor(monkeypatch, {"text": CORTO, "explanation": ""})
    hallazgo = _hallazgo(c)
    conjunto = _conjunto(c)
    sha = _proponer(c, hallazgo).json()["propuesta"]["sha"]
    _tirada(c, conjunto, sha)
    _proveedor(monkeypatch, {"text": "Reservas: normas.", "explanation": ""})
    vista = _proponer(c, hallazgo).json()
    assert vista["propuesta"]["sha"] != sha and vista["probada"] is None
    assert vista["gasto"]["calls"] == 2


def test_un_rechazo_apunta_su_coste_y_no_pisa_la_anterior(api, monkeypatch):
    c, app = api
    _sembrar(app.state.store, "p")
    _conectar(c, monkeypatch, {"agente/reservas.py": AGENTE_CLAUDE})
    hallazgo = _hallazgo(c)
    _proveedor(monkeypatch, {"text": CORTO, "explanation": ""})
    sha = _proponer(c, hallazgo).json()["propuesta"]["sha"]
    _proveedor(monkeypatch, {"text": SISTEMA + " y aún más", "explanation": ""})
    assert _proponer(c, hallazgo).status_code == 422
    vista = c.get("/api/arreglos-texto",
                  params={"project_id": "p", "finding_id": hallazgo["id"]}).json()
    assert vista["propuesta"]["sha"] == sha
    assert vista["gasto"]["calls"] == 2


def test_si_el_codigo_no_dice_lo_que_mando_la_traza_no_hay_propuesta(api, monkeypatch):
    c, app = api
    _sembrar(app.state.store, "p", sistema="Otro sistema que no está en el fichero.")
    _conectar(c, monkeypatch, {"agente/reservas.py": AGENTE_CLAUDE})
    _proveedor(monkeypatch, {"text": CORTO, "explanation": ""})
    r = _proponer(c, _hallazgo(c))
    assert r.status_code == 409, r.text


def test_un_prompt_versionado_acaba_en_una_version_nueva_sin_desplegar(api, monkeypatch):
    c, app = api
    r = c.post("/api/prompts", json={"project_id": "p", "name": "reservas",
                                     "text": "Eres el asistente de {{ hotel }}.\n"
                                             "Estas son las normas de la casa: …"})
    assert r.status_code == 200, r.text
    prompt = r.json()
    _sembrar(app.state.store, "p", prompt=("reservas", 1),
             sistema="Eres el asistente de Sol.\nEstas son las normas de la casa: …")
    _proveedor(monkeypatch, {"text": "Asistente de {{ hotel }}: normas.", "explanation": ""})
    hallazgo = _hallazgo(c)
    conjunto = _conjunto(c)

    vista = _proponer(c, hallazgo).json()
    assert vista["propuesta"]["donde"]["tipo"] == "prompt"
    sha = vista["propuesta"]["sha"]
    r = c.get(f"/api/datasets/{conjunto['id']}/replay",
              params={"model": MODELO, "propuesta": hallazgo["id"]})
    assert r.json()["calls"][0]["messages"][0]["content"] == "Asistente de Sol: normas."

    _tirada(c, conjunto, sha)
    r = c.post("/api/arreglos-texto/aprobar",
               json={"project_id": "p", "finding_id": hallazgo["id"], "sha": sha})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    meta = app.state.metadata
    assert meta.get_prompt_version(prompt["id"], 2).text == "Asistente de {{ hotel }}: normas."
    assert meta.get_prompt(prompt["id"]).production_version == 1


def test_apagado_no_hay_propuesta(api, monkeypatch):
    c, app = api
    monkeypatch.setenv("LAPLACE_DIAGNOSIS_ENABLED", "false")
    from laplace_backend import config

    config.get_settings.cache_clear()
    _sembrar(app.state.store, "p")
    r = _proponer(c, _hallazgo(c))
    assert r.status_code == 503


def test_una_clave_de_proyecto_no_aprueba(tmp_path, monkeypatch):
    from laplace_backend import auth
    from laplace_backend.storage.metadata import SQLiteMetadataStore, new_id

    db = tmp_path / "laplace.db"
    meta = SQLiteMetadataStore(db)
    meta.migrate()
    clave = auth.generate_key()
    meta.create_api_key(new_id("ak"), "p", auth.hash_key(clave), "la del agente")
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(db))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    monkeypatch.setenv("LAPLACE_AUTH_REQUIRED", "true")
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as c:
        r = c.post("/api/arreglos-texto/aprobar",
                   json={"project_id": "p", "finding_id": "x", "sha": "y"},
                   headers={"Authorization": f"Bearer {clave}"})
    config.get_settings.cache_clear()
    assert r.status_code == 403, r.text
