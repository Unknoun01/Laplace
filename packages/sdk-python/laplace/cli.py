"""`laplace`: el producto en local, sin cuenta y sin servidor.

    pip install "laplace-trace[ui]"
    laplace ui

Levanta la ingesta, la API y la interfaz contra un fichero SQLite en `~/.laplace`. Es el
mismo backend que la nube con el almacén cambiado (D-015): las mismas reglas de
detección, el mismo panel de ahorro y la misma pantalla de estado vacío explicando cómo
instrumentar. Nada de esto sube a ninguna parte.

El objetivo medible es que desde no conocer Laplace hasta ver la primera traza pasen
menos de dos minutos sin leer documentación: por eso `laplace ui` no pide argumentos,
crea el fichero solo, abre el navegador y deja escrita la línea que hay que pegar en el
agente.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

from .version import __version__

#: Dónde vive la base local. Un solo sitio, para que borrarlo sea obvio.
HOME = Path(os.getenv("LAPLACE_HOME", Path.home() / ".laplace"))
DEFAULT_PORT = 8100


def _falta_el_extra(exc: Exception) -> int:
    print(
        "laplace ui necesita el extra de interfaz, que no está instalado.\n\n"
        '    pip install "laplace-trace[ui]"\n\n'
        f"(detalle: {exc})",
        file=sys.stderr,
    )
    return 1


def comando_ui(args: argparse.Namespace) -> int:
    """Arranca el backend en modo local y abre el navegador."""
    HOME.mkdir(parents=True, exist_ok=True)
    db = Path(args.db).expanduser() if args.db else HOME / "laplace.db"

    # La configuración del backend se lee del entorno, así que el modo local es
    # exactamente «el mismo proceso con otras variables». No hay un camino aparte.
    os.environ["LAPLACE_STORE"] = "sqlite"
    os.environ["LAPLACE_SQLITE_PATH"] = str(db)
    os.environ["LAPLACE_POSTGRES_ENABLED"] = "false"
    os.environ.setdefault("LAPLACE_LOG_LEVEL", "warning")
    # En local la interfaz y la API salen del mismo origen, así que CORS sobra; se deja
    # abierto para que un agente que exporte desde otro puerto pueda enviar igual.
    os.environ.setdefault("LAPLACE_CORS_ORIGINS", "*")
    # Las alertas a Slack son el mismo código que en la nube y se encienden con las
    # mismas variables (D-075). Aquí sólo se rellena la raíz del enlace, que en local
    # es este propio servidor: sin ella la alerta saldría sin enlace a la ficha.
    os.environ.setdefault("LAPLACE_ALERTS_BASE_URL", f"http://127.0.0.1:{args.port}")

    try:
        import uvicorn
        from laplace_backend.main import app
    except Exception as exc:  # noqa: BLE001
        return _falta_el_extra(exc)

    url = f"http://127.0.0.1:{args.port}"
    print(f"Laplace en local — {db}")
    print(f"  interfaz  {url}")
    print(f"  ingesta   {url} (apunta ahí el SDK)\n")
    print("En tu agente:\n")
    print("    import laplace")
    print(f'    laplace.init(project="mi-agente", endpoint="{url}")\n')

    if os.environ.get("LAPLACE_ALERTS_ENABLED", "").lower() in ("1", "true", "yes"):
        print("Alertas a Slack: activas.\n")

    if not args.no_browser:
        # Un hilo aparte: el navegador se abre cuando el servidor ya escucha, y si no
        # hay navegador (una sesión por ssh) no pasa nada.
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level=os.environ["LAPLACE_LOG_LEVEL"])
    return 0


def comando_demo(args: argparse.Namespace) -> int:
    """Manda unas trazas de ejemplo para ver el producto funcionando sin escribir código.

    Son datos **simulados**, en un proyecto aparte llamado `demo`, y se dice al
    imprimirlo: sirven para enseñar qué detecta Laplace, no para medir nada tuyo.
    """
    from .demo import enviar_trazas_de_ejemplo

    print(f"Enviando trazas simuladas al proyecto «demo» en {args.endpoint} …")
    trazas = enviar_trazas_de_ejemplo(args.endpoint)
    print(f"listo: {trazas} trazas de ejemplo. Son datos inventados, no tuyos.")
    print(f"Ábrelas en {args.endpoint}/?project=demo")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="laplace",
        description="Observabilidad y optimización de agentes de IA, en local.",
    )
    parser.add_argument("--version", action="version", version=f"laplace {__version__}")
    sub = parser.add_subparsers(dest="comando")

    ui = sub.add_parser("ui", help="levanta Laplace en local con SQLite")
    ui.add_argument("--port", type=int, default=DEFAULT_PORT)
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--db", default=None, help=f"por defecto {HOME / 'laplace.db'}")
    ui.add_argument("--no-browser", action="store_true")
    ui.set_defaults(func=comando_ui)

    demo = sub.add_parser("demo", help="manda trazas de ejemplo para ver qué detecta")
    demo.add_argument("--endpoint", default=f"http://127.0.0.1:{DEFAULT_PORT}")
    demo.set_defaults(func=comando_demo)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
