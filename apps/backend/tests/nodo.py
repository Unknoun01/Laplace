"""Ejecutar TypeScript de la web desde una prueba, con Node (D-147).

Node 22.6 o posterior quita los tipos solo. Lo único que no hace es resolver los imports
sin extensión que escribe la web (`./idioma`), y para eso se registra un gancho que
prueba con `.ts`. Sin Node, o con uno viejo, la prueba que lo pida se salta.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[3]
WEB = RAIZ / "apps" / "web"

_GANCHO = (
    "data:text/javascript,"
    "export async function resolve(s, c, n) {"
    " try { return await n(s, c); }"
    " catch (e) { if (s.startsWith('.')) return n(s + '.ts', c); throw e; } }"
)


def ejecutar(cuerpo: str) -> object:
    """Ejecuta `cuerpo` como módulo y devuelve lo que imprima con `salida(valor)`.

    Dentro, `web(ruta)` importa un fichero de `apps/web` (`web("lib/format.ts")`).
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("no hay Node")
    version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout
    mayor, menor = (int(x) for x in version.lstrip("v").split(".")[:2])
    if (mayor, menor) < (22, 6):
        pytest.skip(f"Node {version.strip()} no ejecuta TypeScript")
    guion = f"""
import {{ register }} from "node:module";
import {{ pathToFileURL }} from "node:url";
register({json.dumps(_GANCHO)});
const web = (ruta) => import(pathToFileURL({json.dumps(str(WEB))} + "/" + ruta).href);
const salida = (valor) => process.stdout.write("\\n@@" + JSON.stringify(valor));
{cuerpo}
"""
    r = subprocess.run(
        [node, "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", guion],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=WEB,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.rsplit("\n@@", 1)[1])
