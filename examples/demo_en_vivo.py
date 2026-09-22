"""Los dos agentes a la vez, para mirar Laplace en vivo mientras entran las trazas.

    python examples/demo_en_vivo.py --minutos 15

Arranca `agente_mediocre_ollama.py` y `agente_sano_ollama.py` en paralelo, contra los
mismos tickets, los mismos pedidos y los mismos dos modelos, escribiendo en el mismo
proyecto. Mezcla sus dos salidas en esta terminal, con prefijo, para que se vea cuál va
por dónde. Ctrl+C para todo.

## Antes de lanzarlo

1. Ollama en marcha, con los dos modelos:  `ollama list`
2. Laplace arrancado, en otra terminal:    `python -m laplace.cli ui`
3. En el navegador (http://127.0.0.1:8100): elige el proyecto arriba, entra en
   **Trazas** y pulsa **Ver en vivo**.

Sólo entonces esto. Si Laplace no está escuchando, cada agente lo avisa al arrancar y
las trazas se pierden.

## Qué vas a ver

Las trazas de los dos se intercalan en la lista, distinguibles por el nombre de la
raíz: `atender_ticket` es el mediocre y `responder_consulta` el sano. El mediocre tarda
unas tres veces más y gasta unas tres veces más pasos por el mismo trabajo.

Los dos escriben en el **mismo proyecto** a propósito: así los hallazgos del inicio se
calculan sobre los dos a la vez y se ve que señalan al mediocre por su nombre. Si los
prefieres separados, `LAPLACE_PROJECT` por delante de cada uno.

Ollama atiende una petición cada vez, así que los dos agentes se turnan: la cola es
real y la vas a ver en las duraciones. Es lo que pasaría con un modelo propio en
producción.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import _tienda as t

AQUI = Path(__file__).resolve().parent


def _volcar(proceso: subprocess.Popen, etiqueta: str) -> None:
    """Va escribiendo lo que saca un agente, línea a línea, según sale."""
    for linea in proceso.stdout:  # type: ignore[union-attr]
        texto = linea.rstrip()
        if texto:
            print(texto if texto.startswith("[") else f"[{etiqueta}] {texto}", flush=True)


def _salida_utf8() -> None:
    """Windows escribe a una tubería en cp1252 y revienta con «→» o ««»».

    Pasa sólo cuando la salida NO es una consola —redirigida a un fichero, o leída por
    `demo_en_vivo.py`—, que es justo como se lanza esto. Un acento no puede tumbar un
    agente de ejemplo.
    """
    for flujo in (sys.stdout, sys.stderr):
        # Un flujo sin `reconfigure` (Python viejo, salida redirigida de forma rara) no
        # es motivo para no arrancar.
        with contextlib.suppress(Exception):
            flujo.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutos", type=float, default=15.0)
    parser.add_argument(
        "--pausa-mediocre", type=float, default=3.0, help="segundos entre tickets del mediocre"
    )
    parser.add_argument(
        "--pausa-sano", type=float, default=6.0, help="segundos entre tickets del sano"
    )
    args = parser.parse_args()
    _salida_utf8()

    if not t.comprobar_entorno():
        return 1

    print(f"Proyecto «{t.PROYECTO}» → {t.LAPLACE_ENDPOINT}")
    print(f"Modelos: pequeño {t.PEQUENO} · grande {t.GRANDE}")
    print(f"Abre {t.LAPLACE_ENDPOINT}/trazas/?project={t.PROYECTO} y pulsa «Ver en vivo».")
    print(f"Dos agentes en paralelo durante {args.minutos:g} minutos. Ctrl+C para parar.\n")

    # Sin buffer, o las líneas de los hijos llegarían a bocanadas y la pantalla no se
    # parecería a lo que está pasando.
    comunes = [sys.executable, "-u"]
    # Y por si un hijo imprime antes de reconfigurar su salida.
    entorno = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    procesos = [
        (
            "mediocre",
            subprocess.Popen(
                [*comunes, str(AQUI / "agente_mediocre_ollama.py"),
                 "--minutos", str(args.minutos), "--pausa", str(args.pausa_mediocre)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", cwd=str(AQUI), env=entorno,
            ),
        ),
        (
            "sano",
            subprocess.Popen(
                [*comunes, str(AQUI / "agente_sano_ollama.py"),
                 "--minutos", str(args.minutos), "--pausa", str(args.pausa_sano)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", cwd=str(AQUI), env=entorno,
            ),
        ),
    ]

    hilos = [
        threading.Thread(target=_volcar, args=(proceso, etiqueta), daemon=True)
        for etiqueta, proceso in procesos
    ]
    for hilo in hilos:
        hilo.start()

    try:
        for _, proceso in procesos:
            proceso.wait()
    except KeyboardInterrupt:
        print("\nParando los dos agentes…")
        for _, proceso in procesos:
            proceso.terminate()
        for _, proceso in procesos:
            try:
                proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proceso.kill()
    finally:
        # Un momento para que los hilos vuelquen lo último que quede en la tubería.
        time.sleep(0.3)

    print(f"\nListo. Las trazas están en {t.LAPLACE_ENDPOINT}/?project={t.PROYECTO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
