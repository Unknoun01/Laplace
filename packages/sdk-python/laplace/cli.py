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
    """Carga un mes de datos de ejemplo para ver el producto funcionando sin escribir código.

    Son datos **simulados**, en un proyecto aparte llamado `demo`, y se dice al
    imprimirlo: sirven para enseñar qué hace Laplace, no para medir nada tuyo. Lo carga el
    propio servidor de `laplace ui` (`POST /api/demo`), porque además de las trazas
    escribe prompts, anotaciones y una comparación, que no viajan por la ingesta.
    """
    import json
    import urllib.error
    import urllib.request

    base = args.endpoint.rstrip("/")
    print(f"Cargando un mes de datos simulados en el proyecto «demo» de {base} …")
    peticion = urllib.request.Request(
        f"{base}/api/demo", method="POST", headers={"x-laplace": "cli"}
    )
    try:
        with urllib.request.urlopen(peticion, timeout=600) as respuesta:
            datos = json.loads(respuesta.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(
                "ese servidor no carga datos de ejemplo: sólo existen en `laplace ui`, "
                "no en una instalación compartida.",
                file=sys.stderr,
            )
        else:
            print(f"el servidor ha respondido {exc.code}: {exc.read()[:300]!r}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(
            f"no hay nada escuchando en {base} ({exc.reason}). Arranca antes `laplace ui`.",
            file=sys.stderr,
        )
        return 1

    print(
        f"listo: {datos.get('traces', 0)} ejecuciones en {datos.get('days', 0)} días. "
        "Son datos inventados, no tuyos."
    )
    print(f"Ábrelas en {base}/?project=demo")
    return 0


def comando_replay(args: argparse.Namespace) -> int:
    """Reenvía las llamadas reales de un paso al modelo barato, con tope y permiso."""
    from . import init
    from .evals import EvalError
    from .replay import replay_dataset

    init(project=args.proyecto, endpoint=args.endpoint)

    def permiso(plan) -> bool:
        print(plan.describe())
        if args.si:
            return True
        if not sys.stdin.isatty():
            print(
                "\nNo se gasta nada sin permiso, y aquí no hay nadie a quien preguntar: "
                "vuelve a lanzarlo con --si.",
                file=sys.stderr,
            )
            return False
        return input("\n¿Seguir? [s/N] ").strip().lower() in ("s", "si", "sí", "y", "yes")

    try:
        resultado = replay_dataset(
            args.conjunto,
            model=args.modelo,
            max_usd=args.tope,
            project=args.proyecto,
            endpoint=args.endpoint,
            provider=args.proveedor,
            judge=not args.sin_juez,
            confirm=permiso,
            on_call=lambda n, total: print(f"  {n}/{total}", end="\r", flush=True),
        )
    except EvalError as exc:
        print(f"laplace replay: {exc}", file=sys.stderr)
        return 1
    if resultado.cancelled:
        print("No se ha reenviado nada.")
        return 1
    print(f"\nReenviadas {resultado.calls_done} llamadas; gastado como mucho "
          f"{resultado.spent_usd:.4f} $ (el coste medido sale en la comparación).")
    if resultado.calls_skipped_by_cap:
        print(f"{resultado.calls_skipped_by_cap} no se han reenviado para no pasar del tope.")
    if resultado.cases_failed:
        print(
            f"{resultado.cases_failed} ejecuciones han fallado al reenviarlas "
            "y cuentan como fallo."
        )
    if resultado.judged:
        print(f"El juez da por buenas {resultado.passed} de {resultado.judged} respuestas "
              f"(juzgar ha costado {resultado.judge_cost_usd:.4f} $).")
    elif resultado.judge_note:
        print(resultado.judge_note)
    if resultado.replay_run_id:
        print("Compáralas en la pestaña Probar: "
              f"«{resultado.plan.model}» contra la original ({resultado.replay_run_id}).")
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

    replay = sub.add_parser(
        "replay",
        help="reenvía las llamadas reales de un paso a otro modelo, con tope de gasto",
    )
    replay.add_argument("conjunto", help="nombre o id del conjunto (se guarda en Probar)")
    replay.add_argument("--modelo", required=True, help="el modelo al que reenviarlas")
    replay.add_argument(
        "--tope", type=float, required=True, help="lo máximo que se puede gastar, en dólares"
    )
    replay.add_argument("--proyecto", default=None, help="por defecto, LAPLACE_PROJECT")
    replay.add_argument("--endpoint", default=None, help="por defecto, LAPLACE_ENDPOINT")
    replay.add_argument("--proveedor", choices=("openai", "anthropic"), default=None)
    replay.add_argument("--sin-juez", action="store_true", help="no juzgar las respuestas")
    replay.add_argument("--si", action="store_true", help="no preguntar antes de gastar")
    replay.set_defaults(func=comando_replay)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
