"""El bot de pull requests (D-185), contra un GitHub falso en memoria.

El GitHub falso guarda ramas, ficheros y pull requests, y contesta a las mismas rutas de
la API REST que usa el bot. Lo que se comprueba es lo que vería quien revisa el PR: qué
fichero cambia, qué líneas, desde qué base y en qué rama.
"""

from __future__ import annotations

import base64
import importlib
import inspect
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from laplace.schema import Cost, LLMAttributes, Span, TokenUsage

from laplace_backend import github_bot as gb
from laplace_backend.github_bot import GitHubError, SinPropuesta

AGENTE = '''import os

from openai import OpenAI

cliente = OpenAI()


def resumir(texto):
    respuesta = cliente.chat.completions.create(
        model="gpt-5.5",
        messages=[{"role": "user", "content": texto}],
    )
    return respuesta.choices[0].message.content


def clasificar(texto):
    return cliente.chat.completions.create(model="gpt-5.5", messages=[])
'''


class GitHubFalso:
    """Lo justo de la API REST de GitHub para un repositorio."""

    def __init__(self, ficheros: dict[str, str], *, rama: str = "main") -> None:
        self.principal = rama
        self.commits: dict[str, dict[str, str]] = {"c0": dict(ficheros)}
        self.ramas: dict[str, str] = {rama: "c0"}
        self.prs: list[dict[str, Any]] = []
        self.llamadas: list[tuple[str, str, str]] = []
        self.tokens_validos = {"tok-usuario"}

    def __call__(self, metodo: str, url: str, token: str, cuerpo: dict | None) -> Any:
        self.llamadas.append((metodo, url, token))
        ruta = url.removeprefix(gb.API)
        if ruta.startswith("/app/installations/"):
            assert token.count(".") == 2, "a la App se le habla con un JWT"
            self.tokens_validos.add("tok-instalacion")
            return {"token": "tok-instalacion"}
        if token not in self.tokens_validos:
            raise GitHubError("github.sin_permiso")
        ruta = ruta.removeprefix("/repos/acme/agentes")
        if metodo == "GET" and ruta == "":
            return {"default_branch": self.principal}
        if metodo == "GET" and ruta.startswith("/pulls"):
            cabeza = re.search(r"head=([^&]+)", ruta).group(1).replace("%3A", ":")
            return [p for p in self.prs if f"acme:{p['head']}" == cabeza and p["open"]]
        if metodo == "GET" and ruta.startswith("/git/ref/heads/"):
            rama = ruta.removeprefix("/git/ref/heads/")
            if rama not in self.ramas:
                raise GitHubError("github.no_encontrado")
            return {"object": {"sha": self.ramas[rama]}}
        if metodo == "GET" and ruta.startswith("/git/trees/"):
            sha = ruta.removeprefix("/git/trees/").split("?")[0]
            return {"tree": [{"path": p, "type": "blob"} for p in self.commits[sha]]}
        if metodo == "GET" and ruta.startswith("/contents/"):
            camino, ref = ruta.removeprefix("/contents/").split("?ref=")
            contenido = self.commits[self.ramas[ref]][camino]
            return {
                "content": base64.b64encode(contenido.encode()).decode(),
                "sha": f"blob-{camino}",
            }
        if metodo == "POST" and ruta == "/git/refs":
            rama = cuerpo["ref"].removeprefix("refs/heads/")
            if rama in self.ramas:
                raise GitHubError("github.rechazado")
            self.ramas[rama] = cuerpo["sha"]
            return {}
        if metodo == "PATCH" and ruta.startswith("/git/refs/heads/"):
            self.ramas[ruta.removeprefix("/git/refs/heads/")] = cuerpo["sha"]
            return {}
        if metodo == "PUT" and ruta.startswith("/contents/"):
            camino = ruta.removeprefix("/contents/")
            padre = self.ramas[cuerpo["branch"]]
            assert cuerpo["sha"] == f"blob-{camino}", "se escribe sobre el fichero leído"
            nuevo = f"c{len(self.commits)}"
            self.commits[nuevo] = {
                **self.commits[padre],
                camino: base64.b64decode(cuerpo["content"]).decode(),
            }
            self.ramas[cuerpo["branch"]] = nuevo
            return {}
        if metodo == "POST" and ruta == "/pulls":
            numero = len(self.prs) + 1
            self.prs.append({**cuerpo, "number": numero, "open": True,
                             "html_url": f"https://github.com/acme/agentes/pull/{numero}"})
            return self.prs[-1]
        raise AssertionError(f"ruta no prevista: {metodo} {ruta}")

    def fichero(self, rama: str, camino: str) -> str:
        return self.commits[self.ramas[rama]][camino]


AJUSTES = {"repo": "acme/agentes", "token": "tok-usuario"}


def _abrir(github: GitHubFalso, ajustes: dict | None = None, **cambios: Any) -> gb.PullRequest:
    datos = {
        "de": "gpt-5.5",
        "a": "gpt-5.6-luna",
        "ruta_anotada": "agente/resumen.py",
        "linea": 9,
        "titulo": "Cambiar de modelo el paso resumir",
        "cuerpo": "Ahorro medido: 12 $ en 7 días.",
        "mensaje": "laplace: gpt-5.5 → gpt-5.6-luna en resumir",
    }
    datos.update(cambios)
    return gb.abrir(ajustes or AJUSTES, "modelo_caro:abc", pedir=github, **datos)


# ---------------------------------------------------------------------------------
# El cambio en el texto
# ---------------------------------------------------------------------------------


def test_cambia_el_modelo_de_la_llamada_anotada_y_nada_mas():
    nuevo = gb.cambiar_modelo(AGENTE, 9, "gpt-5.5", "gpt-5.6-luna")
    cambiadas = [
        (a, b) for a, b in zip(AGENTE.splitlines(), nuevo.splitlines(), strict=True) if a != b
    ]
    assert cambiadas == [('        model="gpt-5.5",', '        model="gpt-5.6-luna",')]


def test_la_otra_llamada_con_el_mismo_modelo_no_se_toca():
    """`clasificar` usa el mismo modelo, pero el hallazgo es de `resumir`."""
    nuevo = gb.cambiar_modelo(AGENTE, 17, "gpt-5.5", "gpt-5.6-luna")
    assert 'model="gpt-5.5",\n' in nuevo
    assert 'create(model="gpt-5.6-luna", messages=[])' in nuevo


def test_una_constante_unica_arriba_se_cambia():
    fuente = 'MODELO = "gpt-5.5"\n\n' + "\n" * 60 + "cliente.create(model=MODELO)\n"
    nuevo = gb.cambiar_modelo(fuente, 63, "gpt-5.5", "gpt-5.6-luna")
    assert nuevo.startswith('MODELO = "gpt-5.6-luna"\n')


def test_si_el_modelo_no_esta_escrito_no_se_adivina():
    fuente = "import os\n\ncliente.create(model=os.environ['MODELO'])\n"
    with pytest.raises(SinPropuesta) as no:
        gb.cambiar_modelo(fuente, 3, "gpt-5.5", "gpt-5.6-luna")
    assert no.value.clave == "pr.modelo_no_literal"


def test_si_esta_varias_veces_lejos_de_la_llamada_no_se_elige_una():
    fuente = 'A = "gpt-5.5"\nB = "gpt-5.5"\n' + "\n" * 60 + "cliente.create(model=A)\n"
    with pytest.raises(SinPropuesta) as no:
        gb.cambiar_modelo(fuente, 63, "gpt-5.5", "gpt-5.6-luna")
    assert no.value.clave == "pr.modelo_varias_veces"


def test_un_modelo_que_contiene_al_otro_no_se_confunde():
    """`gpt-5.5` no es el principio de `gpt-5.5-pro`: se cambia el literal entero."""
    fuente = 'x = create(model="gpt-5.5-pro")\n'
    with pytest.raises(SinPropuesta):
        gb.cambiar_modelo(fuente, 1, "gpt-5.5", "gpt-5.6-luna")


@pytest.mark.parametrize(
    ("anotada", "esperada"),
    [
        ("agente/resumen.py", "servicios/agente/resumen.py"),
        ("servicios/agente/resumen.py", "servicios/agente/resumen.py"),
        ("resumen.py", "servicios/agente/resumen.py"),
    ],
)
def test_la_ruta_se_encuentra_por_sufijo(anotada, esperada):
    arbol = ["servicios/agente/resumen.py", "servicios/otroagente/clasificar.py", "README.md"]
    assert gb.ubicar(anotada, arbol) == esperada


def test_la_ruta_por_sufijo_va_por_tramos_enteros():
    with pytest.raises(SinPropuesta) as no:
        gb.ubicar("agente/resumen.py", ["servicios/otroagente/resumen.py"])
    assert no.value.clave == "pr.no_esta_en_repo"


def test_una_ruta_que_casa_dos_veces_no_se_elige():
    with pytest.raises(SinPropuesta) as no:
        gb.ubicar("resumen.py", ["a/resumen.py", "b/resumen.py"])
    assert no.value.clave == "pr.ruta_ambigua"


def test_el_sitio_es_el_mas_repetido_entre_las_llamadas_de_ese_modelo():
    def span(ruta: str, linea: int, modelo: str = "gpt-5.5") -> Span:
        return Span(
            trace_id="t",
            span_id=f"{ruta}{linea}{modelo}",
            project_id="p",
            name="chat",
            type="llm",
            start_time="2026-10-01T00:00:00Z",
            end_time="2026-10-01T00:00:01Z",
            llm=LLMAttributes(request_model=modelo),
            attributes={gb.ATTR_RUTA: ruta, gb.ATTR_LINEA: linea},
        )

    evidencia = [span("a.py", 9), span("a.py", 9), span("b.py", 4), span("c.py", 1, "otro")]
    assert gb.sitio(evidencia, "gpt-5.5") == ("a.py", 9)
    with pytest.raises(SinPropuesta) as no:
        gb.sitio([], "gpt-5.5")
    assert no.value.clave == "pr.sin_sitio"


# ---------------------------------------------------------------------------------
# El pull request
# ---------------------------------------------------------------------------------


def test_abre_un_pr_desde_la_rama_principal_con_el_cambio():
    github = GitHubFalso({"servicios/agente/resumen.py": AGENTE, "README.md": "hola"})
    pr = _abrir(github)
    assert pr.url == "https://github.com/acme/agentes/pull/1"
    assert pr.ya_abierto is False
    rama = gb.rama_para("modelo_caro:abc")
    assert github.prs[0]["head"] == rama
    assert github.prs[0]["base"] == "main"
    assert github.prs[0]["title"] == "Cambiar de modelo el paso resumir"
    # La rama principal no se toca; la del PR lleva sólo ese cambio.
    assert github.fichero("main", "servicios/agente/resumen.py") == AGENTE
    assert github.fichero(rama, "servicios/agente/resumen.py") == gb.cambiar_modelo(
        AGENTE, 9, "gpt-5.5", "gpt-5.6-luna"
    )
    assert github.fichero(rama, "README.md") == "hola"


def test_pedirlo_dos_veces_devuelve_el_mismo_pr():
    github = GitHubFalso({"agente/resumen.py": AGENTE})
    primero = _abrir(github)
    escrituras = sum(1 for m, _, _ in github.llamadas if m in ("PUT", "POST", "PATCH"))
    segundo = _abrir(github)
    assert segundo.url == primero.url
    assert segundo.ya_abierto is True
    assert len(github.prs) == 1
    assert sum(1 for m, _, _ in github.llamadas if m in ("PUT", "POST", "PATCH")) == escrituras


def test_una_rama_que_ya_existe_se_reaprovecha_desde_la_base():
    """Un PR anterior cerrado deja la rama: se vuelve a poner sobre la base de hoy."""
    github = GitHubFalso({"agente/resumen.py": AGENTE})
    _abrir(github)
    github.prs[0]["open"] = False
    github.commits["c9"] = {"agente/resumen.py": AGENTE.replace("texto", "entrada")}
    github.ramas["main"] = "c9"
    pr = _abrir(github)
    assert pr.numero == 2
    rama = gb.rama_para("modelo_caro:abc")
    assert "entrada" in github.fichero(rama, "agente/resumen.py")
    assert 'model="gpt-5.6-luna"' in github.fichero(rama, "agente/resumen.py")


def test_con_la_rama_base_configurada_se_usa_esa():
    github = GitHubFalso({"agente/resumen.py": AGENTE}, rama="develop")
    pr = _abrir(github, {**AJUSTES, "base_branch": "develop"})
    assert github.prs[0]["base"] == "develop"
    assert pr.numero == 1
    assert not any(url.endswith("/repos/acme/agentes") for _, url, _ in github.llamadas)


def test_sin_propuesta_no_se_crea_ninguna_rama():
    github = GitHubFalso({"agente/resumen.py": "cliente.create(model=os.environ['M'])\n"})
    with pytest.raises(SinPropuesta):
        _abrir(github, linea=1)
    assert set(github.ramas) == {"main"}
    assert github.prs == []


def test_un_token_rechazado_es_un_error_con_su_motivo():
    github = GitHubFalso({"agente/resumen.py": AGENTE})
    with pytest.raises(GitHubError) as error:
        _abrir(github, {**AJUSTES, "token": "tok-caducado"})
    assert error.value.clave == "github.sin_permiso"


def test_un_repo_mal_escrito_no_llega_a_github():
    github = GitHubFalso({})
    for repo in ("", "acme", "acme/agentes/extra", "../acme/x"):
        with pytest.raises(GitHubError):
            _abrir(github, {**AJUSTES, "repo": repo})
    assert github.llamadas == []


# ---------------------------------------------------------------------------------
# La GitHub App
# ---------------------------------------------------------------------------------


def _clave_rsa() -> tuple[Any, str]:
    rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    from cryptography.hazmat.primitives import serialization

    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = clave.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return clave, pem


def _partes(jwt: str) -> tuple[dict, dict, bytes, bytes]:
    def b64(s: str) -> bytes:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

    cab, carga, firma = jwt.split(".")
    return json.loads(b64(cab)), json.loads(b64(carga)), b64(firma), f"{cab}.{carga}".encode()


def test_el_jwt_de_la_app_va_firmado_con_su_clave_y_caduca_pronto():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    clave, pem = _clave_rsa()
    jwt = gb.jwt_app("12345", pem, ahora=1_800_000_000)
    cabecera, carga, firma, firmado = _partes(jwt)
    assert cabecera == {"alg": "RS256", "typ": "JWT"}
    assert carga == {"iat": 1_800_000_000 - 60, "exp": 1_800_000_000 + 540, "iss": "12345"}
    clave.public_key().verify(firma, firmado, padding.PKCS1v15(), hashes.SHA256())


def test_una_clave_que_no_es_una_clave_es_un_error_con_su_motivo():
    pytest.importorskip("cryptography")
    with pytest.raises(GitHubError) as error:
        gb.jwt_app("1", "-----BEGIN PRIVATE KEY-----\nbasura\n-----END PRIVATE KEY-----")
    assert error.value.clave == "github.clave_app_invalida"


def test_con_la_app_se_pide_un_token_de_la_instalacion_y_se_usa_ese(monkeypatch):
    _, pem = _clave_rsa()
    monkeypatch.setenv("LAPLACE_GITHUB_APP_ID", "12345")
    monkeypatch.setenv("LAPLACE_GITHUB_APP_PRIVATE_KEY", pem.replace("\n", "\\n"))
    github = GitHubFalso({"agente/resumen.py": AGENTE})
    _abrir(github, {"repo": "acme/agentes", "installation_id": 777})
    primera = github.llamadas[0]
    assert primera[0] == "POST"
    assert primera[1] == f"{gb.API}/app/installations/777/access_tokens"
    assert {token for _, _, token in github.llamadas[1:]} == {"tok-instalacion"}


def test_con_instalacion_pero_sin_app_en_el_entorno_se_dice(monkeypatch):
    monkeypatch.delenv("LAPLACE_GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("LAPLACE_GITHUB_APP_PRIVATE_KEY", raising=False)
    with pytest.raises(GitHubError) as error:
        _abrir(GitHubFalso({}), {"repo": "acme/agentes", "installation_id": 777})
    assert error.value.clave == "github.sin_app"


# ---------------------------------------------------------------------------------
# De punta a punta: el SDK anota el sitio, la API abre el PR
# ---------------------------------------------------------------------------------


def test_el_sdk_anota_el_fichero_y_la_linea_de_la_llamada():
    """La ruta, relativa al repositorio; la línea, la de la llamada en este fichero."""
    openai = pytest.importorskip("openai")
    import httpx
    from helpers import span_llm
    from laplace.integrations import openai as oi

    respuesta = {
        "id": "x",
        "object": "chat.completion",
        "created": 1770000000,
        "model": "gpt-5.5",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }
    cliente = openai.OpenAI(
        api_key="sk-de-mentira",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=respuesta))
        ),
    )
    oi.instrument()
    try:
        linea = inspect.currentframe().f_lineno + 1
        cliente.chat.completions.create(model="gpt-5.5", messages=[])
    finally:
        oi.uninstrument()
    attrs = span_llm().attributes
    assert attrs[gb.ATTR_RUTA] == "apps/backend/tests/test_github_bot.py"
    assert attrs[gb.ATTR_LINEA] == linea
    assert attrs["code.function.name"] == "test_el_sdk_anota_el_fichero_y_la_linea_de_la_llamada"


AHORA = datetime.now(timezone.utc)


def _proyecto_caro(store: Any, project: str, ruta: str, linea: int) -> None:
    """Diez ejecuciones de un paso corto con un modelo caro: la regla del modelo caro."""
    spans = []
    for n in range(10):
        traza = uuid.uuid4().hex
        inicio = AHORA - timedelta(hours=3, minutes=n)
        raiz = f"a{n:03d}".ljust(16, "0")
        spans.append(
            Span(
                span_id=raiz,
                trace_id=traza,
                project_id=project,
                name="agente",
                type="agent",
                status="ok",
                start_time=inicio,
                end_time=inicio + timedelta(seconds=1),
                duration_ms=1000,
            )
        )
        spans.append(
            Span(
                span_id=f"b{n:03d}".ljust(16, "0"),
                parent_span_id=raiz,
                trace_id=traza,
                project_id=project,
                name="resumir",
                type="llm",
                status="ok",
                start_time=inicio,
                end_time=inicio + timedelta(milliseconds=500),
                duration_ms=500,
                dedup_hash=f"h{n}",
                llm=LLMAttributes(
                    system="openai",
                    request_model="gpt-5.6-terra",
                    response_model="gpt-5.6-terra",
                    usage=TokenUsage(input_tokens=200, output_tokens=10),
                    cost=Cost(input_usd=0.0005, output_usd=0.0005, total_usd=0.001),
                ),
                attributes={gb.ATTR_RUTA: ruta, gb.ATTR_LINEA: linea},
            )
        )
    store.insert_spans(spans)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(tmp_path / "laplace.db"))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    from fastapi.testclient import TestClient

    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c, main.app
    config.get_settings.cache_clear()


def test_desde_la_ficha_se_abre_el_pr_con_el_cambio(api, monkeypatch):
    c, app = api
    _proyecto_caro(app.state.store, "p", "agente/resumen.py", 9)
    github = GitHubFalso(
        {"servicios/agente/resumen.py": AGENTE.replace("gpt-5.5", "gpt-5.6-terra")}
    )
    monkeypatch.setattr(gb, "_pedir", github)

    r = c.put(
        "/api/github", json={"project_id": "p", "repo": "acme/agentes", "token": "tok-usuario"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is True
    assert "tok-usuario" not in r.text, "el token no vuelve nunca entero"
    assert "tok-usuario" not in c.get("/api/github", params={"project_id": "p"}).text

    hallazgos = c.get("/api/overview", params={"project_id": "p", "days": 7}).json()["findings"]
    caro = next(h for h in hallazgos if h["kind"] == "modelo_caro")
    assert caro["model_change"]["from"] == "gpt-5.6-terra"

    r = c.post("/api/github/pull-request", json={"project_id": "p", "finding_id": caro["id"]})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "url": "https://github.com/acme/agentes/pull/1",
        "number": 1,
        "already_open": False,
    }
    rama = gb.rama_para(caro["id"])
    nuevo = github.fichero(rama, "servicios/agente/resumen.py")
    assert f'model="{caro["model_change"]["to"]}",' in nuevo
    assert caro["title"] in github.prs[0]["body"]

    otra = c.post("/api/github/pull-request", json={"project_id": "p", "finding_id": caro["id"]})
    assert otra.json()["already_open"] is True
    assert len(github.prs) == 1


def test_sin_repositorio_conectado_no_se_abre_nada(api):
    c, app = api
    _proyecto_caro(app.state.store, "p", "agente/resumen.py", 9)
    hallazgos = c.get("/api/overview", params={"project_id": "p", "days": 7}).json()["findings"]
    caro = next(h for h in hallazgos if h["kind"] == "modelo_caro")
    r = c.post("/api/github/pull-request", json={"project_id": "p", "finding_id": caro["id"]})
    assert r.status_code == 409
    assert "Ajustes" in r.json()["detail"] or "Settings" in r.json()["detail"]


def test_sin_sitio_anotado_se_dice_por_que(api, monkeypatch):
    """Un agente con un SDK de antes de D-185 no anota la línea: no se adivina."""
    c, app = api
    _proyecto_caro(app.state.store, "p", "", 0)
    monkeypatch.setattr(gb, "_pedir", GitHubFalso({"agente/resumen.py": AGENTE}))
    c.put("/api/github", json={"project_id": "p", "repo": "acme/agentes", "token": "tok-usuario"})
    hallazgos = c.get("/api/overview", params={"project_id": "p", "days": 7}).json()["findings"]
    caro = next(h for h in hallazgos if h["kind"] == "modelo_caro")
    r = c.post("/api/github/pull-request", json={"project_id": "p", "finding_id": caro["id"]})
    assert r.status_code == 409
    assert "SDK" in r.json()["detail"]


def test_un_repositorio_mal_escrito_no_se_guarda(api):
    c, _ = api
    r = c.put("/api/github", json={"project_id": "p", "repo": "no-es-un-repo", "token": "x"})
    assert r.status_code == 422
    assert c.get("/api/github", params={"project_id": "p"}).json()["configured"] is False


def test_un_paquete_instalado_fuera_del_entorno_tampoco_es_codigo_del_usuario(tmp_path):
    """`pip install --user` deja los paquetes en un `site-packages` que no es el del
    entorno: una llamada desde LangChain instalado ahí no es la línea que hay que tocar."""
    from laplace import _sitio

    ajeno = tmp_path / "usuario" / "site-packages" / "langchain" / "llamar.py"
    propio = tmp_path / "proyecto" / "agente.py"
    assert _sitio._es_ajeno(str(ajeno)) is True
    assert _sitio._es_ajeno(str(propio)) is False
    assert _sitio._es_ajeno("<frozen runpy>") is True
