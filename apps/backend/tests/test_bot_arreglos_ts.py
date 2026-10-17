"""El bot de PR escribe también TypeScript y JavaScript, sobre el árbol de `tree-sitter` (D-220).

Mismo trato que en Python (D-190): se toca sólo la llamada anotada, lo ambiguo no se
propone y se dice por qué, y el fichero resultante tiene que seguir parseando sin errores.
Sin el extra `[typescript]`, el bot dice que no puede, nunca escribe a ojo.
"""

from __future__ import annotations

import pytest

from laplace_backend import arreglos_ts as at
from laplace_backend import github_bot as gb
from laplace_backend.github_bot import SinPropuesta

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_typescript")
pytest.importorskip("tree_sitter_javascript")

AGENTE_TS = '''import Anthropic from "@anthropic-ai/sdk";

const cliente = new Anthropic();
// Antes usábamos model: "claude-sonnet-4-5" aquí.
const SISTEMA = `Eres el asistente de reservas ñ.
Normas de la casa.`;

export async function responder(pregunta: string): Promise<string> {
  const respuesta = await cliente.messages.create({
    model: "claude-sonnet-4-5",
    max_tokens: 400,
    system: SISTEMA,
    messages: [{ role: "user", content: pregunta }],
  });
  return respuesta.content[0].text;
}
'''
LINEA = 9  # `const respuesta = await cliente.messages.create({`


def _con(llamada: str) -> str:
    return f"const c = new Anthropic();\nasync function f() {{\n  {llamada}\n}}\n"


# --- cambio de modelo -------------------------------------------------------------


def test_cambia_el_modelo_de_la_llamada_y_no_el_comentario():
    nuevo = at.cambiar_modelo(AGENTE_TS, LINEA, "claude-sonnet-4-5", "claude-haiku-4-5", "a.ts")
    assert 'model: "claude-haiku-4-5",' in nuevo
    assert "// Antes usábamos model: \"claude-sonnet-4-5\" aquí." in nuevo  # el comentario, intacto
    assert nuevo.count("claude-sonnet-4-5") == 1


def test_modelo_en_javascript_con_comillas_simples_y_clave_entrecomillada():
    js = _con("await c.chat.completions.create({ 'model': 'gpt-5.5', messages: [] });")
    nuevo = at.cambiar_modelo(js, 3, "gpt-5.5", "gpt-5.6-luna", "a.js")
    assert "'model': 'gpt-5.6-luna'" in nuevo


def test_modelo_que_viene_de_una_variable_no_se_toca():
    js = _con("await c.chat.completions.create({ model: MODELO, messages: [] });")
    with pytest.raises(SinPropuesta) as e:
        at.cambiar_modelo(js, 3, "gpt-5.5", "otro", "a.js")
    assert e.value.clave == "pr.modelo_no_literal"


def test_modelo_en_plantilla_con_sustitucion_no_se_toca():
    js = _con("await c.chat.completions.create({ model: `gpt-${v}`, messages: [] });")
    with pytest.raises(SinPropuesta) as e:
        at.cambiar_modelo(js, 3, "gpt-5.5", "otro", "a.js")
    assert e.value.clave == "pr.modelo_no_literal"


def test_llamada_envuelta_en_una_funcion_propia_no_se_toca():
    js = _con("await envolver(c, { model: 'gpt-5.5' });")
    with pytest.raises(SinPropuesta) as e:
        at.cambiar_modelo(js, 3, "gpt-5.5", "otro", "a.js")
    assert e.value.clave == "pr.sin_llamada"


# --- cache_control ----------------------------------------------------------------


def test_system_de_texto_en_variable_se_envuelve_en_bloque_con_cache():
    nuevo = at.poner_cache(AGENTE_TS, LINEA, "a.ts")
    assert (
        'system: [{ type: "text", text: SISTEMA, cache_control: { type: "ephemeral" } }],' in nuevo
    )
    assert nuevo.replace(
        'system: [{ type: "text", text: SISTEMA, cache_control: { type: "ephemeral" } }],',
        "system: SISTEMA,",
    ) == AGENTE_TS


def test_system_literal_con_tildes_antes_en_la_linea():
    js = _con("await c.messages.create({ model: 'x', system: 'señor ñandú', messages: [] });")
    nuevo = at.poner_cache(js, 3, "a.js")
    assert "text: 'señor ñandú', cache_control" in nuevo


def test_lista_de_bloques_lleva_cache_en_el_ultimo():
    js = _con(
        "await c.messages.create({ model: 'claude-x', messages: [],\n"
        "    system: [{ type: 'text', text: 'a' }, { type: 'text', text: 'b', }] });"
    )
    nuevo = at.poner_cache(js, 3, "a.js")
    assert "{ type: 'text', text: 'a' }, { type: 'text', text: 'b', cache_control" in nuevo


def test_system_que_sale_de_una_funcion_no_se_toca():
    js = _con("await c.messages.create({ model: 'claude-x', system: cargar(), messages: [] });")
    with pytest.raises(SinPropuesta) as e:
        at.poner_cache(js, 3, "a.js")
    assert e.value.clave == "pr.system_desconocido"


def test_ya_cachea_y_sin_system():
    ya = _con(
        "await c.messages.create({ model: 'x', system: [{ type: 'text', text: 'a',"
        " cache_control: { type: 'ephemeral' } }] });"
    )
    with pytest.raises(SinPropuesta) as e:
        at.poner_cache(ya, 3, "a.js")
    assert e.value.clave == "pr.ya_cachea"
    with pytest.raises(SinPropuesta) as e:
        at.poner_cache(_con("await c.messages.create({ model: 'x', messages: [] });"), 3, "a.js")
    assert e.value.clave == "pr.sin_system"


def test_tsx_y_jsx_se_parsean():
    tsx = _con("await c.messages.create({ model: 'm', system: 'hola', messages: [] });")
    assert "cache_control" in at.poner_cache(tsx, 3, "pagina.tsx")
    assert "cache_control" in at.poner_cache(tsx, 3, "pagina.jsx")


def test_fichero_que_ya_no_parsea_no_se_propone():
    with pytest.raises(SinPropuesta) as e:
        at.poner_cache("const = = (", 1, "a.ts")
    assert e.value.clave == "pr.no_compila"


def test_comprobar_rechaza_un_resultado_con_errores_de_sintaxis():
    with pytest.raises(SinPropuesta) as e:
        at.comprobar("const x = ;;; (", "a.ts")
    assert e.value.clave == "pr.no_compila"


# --- sin el extra y despacho por lenguaje -------------------------------------------


def test_sin_el_extra_el_bot_lo_dice_y_no_escribe(monkeypatch):
    monkeypatch.setattr(at, "_importar", lambda: (_ for _ in ()).throw(ImportError("tree_sitter")))
    with pytest.raises(SinPropuesta) as e:
        at.cambiar_modelo(AGENTE_TS, LINEA, "claude-sonnet-4-5", "x", "a.ts")
    assert e.value.clave == "pr.ts_sin_extra"
    with pytest.raises(SinPropuesta) as e:
        at.poner_cache(AGENTE_TS, LINEA, "a.ts")
    assert e.value.clave == "pr.ts_sin_extra"


def test_el_tope_de_vueltas_no_se_escribe_en_ts():
    with pytest.raises(SinPropuesta) as e:
        at.poner_tope()
    assert e.value.clave == "pr.tope_no_ts"


def test_abrir_un_pr_en_typescript_contra_el_github_falso():
    from test_github_bot import AJUSTES, GitHubFalso

    github = GitHubFalso({"src/agente.ts": AGENTE_TS})
    pr = gb.abrir(
        AJUSTES, "modelo_caro:ts", pedir=github, ruta_anotada="/srv/app/src/agente.ts",
        de="claude-sonnet-4-5", a="claude-haiku-4-5", linea=LINEA,
        titulo="t", cuerpo="c", mensaje="m",
    )
    assert 'model: "claude-haiku-4-5"' in github.fichero(github.prs[0]["head"], "src/agente.ts")
    assert pr.numero == 1


def test_abrir_cache_en_ts_dispara_comprobacion_y_sin_extra_no_llega_a_github(monkeypatch):
    from test_github_bot import AJUSTES, GitHubFalso

    monkeypatch.setattr(at, "_importar", lambda: (_ for _ in ()).throw(ImportError("x")))
    github = GitHubFalso({"src/agente.ts": AGENTE_TS})
    with pytest.raises(SinPropuesta) as e:
        gb.abrir(
            AJUSTES, "modelo_caro:ts", pedir=github, ruta_anotada="src/agente.ts",
            de="claude-sonnet-4-5", a="x", linea=LINEA, titulo="t", cuerpo="c", mensaje="m",
        )
    assert e.value.clave == "pr.ts_sin_extra"
    assert github.llamadas == []  # se sabe antes de pedir nada a GitHub


def test_plantilla_con_sustitucion_no_es_un_literal_ni_con_el_mismo_texto():
    js = _con("await c.chat.completions.create({ model: `gpt-${v}`, messages: [] });")
    with pytest.raises(SinPropuesta) as e:
        at.cambiar_modelo(js, 3, "gpt-${v}", "otro", "a.js")
    assert e.value.clave == "pr.modelo_no_literal"


def test_system_declarado_dos_veces_no_se_elige_uno():
    js = (
        "let S = 'a';\nS = 'b';\nlet S2 = 1;\n"
        "async function f() {\n  const S = 'a';\n  { const S = 'b'; }\n"
        "  await c.messages.create({ model: 'x', system: S, messages: [] });\n}\n"
    )
    with pytest.raises(SinPropuesta) as e:
        at.poner_cache(js, 7, "a.js")
    assert e.value.clave == "pr.system_desconocido"
