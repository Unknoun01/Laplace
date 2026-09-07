"""Mete la interfaz dentro del paquete de Python.

    python scripts/build_ui.py

Exporta la aplicación de Next a HTML estático y copia el resultado a
`packages/sdk-python/laplace/ui`, que es de donde lo sirve `laplace ui` (D-069). Es un
paso de **publicación**, no de desarrollo: en el repositorio, `laplace ui` encuentra
`apps/web/out` por su cuenta, así que esto sólo hace falta antes de construir el wheel.

Node hace falta aquí y sólo aquí. Quien instale el paquete no lo necesita: se lleva el
HTML ya hecho.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
WEB = RAIZ / "apps" / "web"
DESTINO = RAIZ / "packages" / "sdk-python" / "laplace" / "ui"


def main() -> int:
    if not (WEB / "node_modules").exists():
        print("falta instalar las dependencias de apps/web (npm install)", file=sys.stderr)
        return 1

    entorno = {**os.environ, "LAPLACE_EXPORT": "1"}
    print("exportando la interfaz…")
    resultado = subprocess.run(
        ["npx", "next", "build"], cwd=WEB, env=entorno, shell=os.name == "nt", check=False
    )
    if resultado.returncode != 0:
        return resultado.returncode

    salida = WEB / "out"
    if not (salida / "index.html").exists():
        print(f"el build no ha dejado nada en {salida}", file=sys.stderr)
        return 1

    if DESTINO.exists():
        shutil.rmtree(DESTINO)
    shutil.copytree(salida, DESTINO)
    ficheros = sum(1 for _ in DESTINO.rglob("*") if _.is_file())
    print(f"interfaz copiada a {DESTINO} ({ficheros} ficheros)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
