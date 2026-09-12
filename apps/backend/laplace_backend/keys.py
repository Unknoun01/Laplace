"""Crear, listar y revocar claves de API.

    python -m laplace_backend.keys create --project mi-agente --name "ingesta prod"
    python -m laplace_backend.keys create --project '*' --name "panel del operador"
    python -m laplace_backend.keys list
    python -m laplace_backend.keys revoke ak_3f2a…

Es una herramienta de línea de comandos y **no un endpoint** a propósito. Un endpoint
que crea credenciales necesita a su vez una credencial que lo proteja, y esa primera
credencial tiene que salir de algún sitio: o de una variable de entorno con una clave
maestra —un segundo camino de validación, que es un segundo sitio donde equivocarse— o
de un acceso a la base, que es exactamente esto. Con `docker compose exec backend` se
crea la primera clave en diez segundos y no hay ninguna ruta que pueda emitir poderes.

La clave se enseña **una vez**: lo que se guarda es su SHA-256, así que si se pierde no
se puede recuperar, sólo revocar y crear otra. Eso es lo correcto y conviene decirlo en
el momento en que se imprime.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .auth import ALL_PROJECTS, generate_key, hash_key
from .config import get_settings
from .storage.metadata import new_id


def _store() -> Any:
    from .storage.postgres import build_metadata_store

    settings = get_settings()
    almacen = build_metadata_store(settings)
    almacen.migrate()
    return almacen


def crear(args: argparse.Namespace) -> int:
    clave = generate_key()
    key_id = new_id("ak")
    _store().create_api_key(key_id, args.project, hash_key(clave), args.name or "")

    alcance = (
        "todos los proyectos de esta instalación"
        if args.project == ALL_PROJECTS
        else f"el proyecto «{args.project}»"
    )
    print(f"\nClave creada para {alcance}.\n")
    print(f"    {clave}\n")
    print("Apúntala ahora: sólo se guarda su hash, así que no se puede volver a ver.")
    print("Se manda en la cabecera «Authorization: Bearer <clave>».\n")
    print("En el SDK:")
    print(f'    laplace.init(project="{args.project}", api_key="{clave[:12]}…")\n')
    print(f"Identificador para revocarla: {key_id}")
    return 0


def listar(args: argparse.Namespace) -> int:
    filas = _store().list_api_keys(args.project)
    if not filas:
        print("No hay claves. Crea una con `create --project <id>`.")
        return 0
    print(f"{'id':<20} {'proyecto':<24} {'estado':<10} nombre")
    for f in filas:
        estado = "revocada" if f.get("revoked_at") else "activa"
        print(f"{f['id']:<20} {f['project_id']:<24} {estado:<10} {f.get('name') or ''}")
    return 0


def revocar(args: argparse.Namespace) -> int:
    if _store().revoke_api_key(args.key_id):
        print(f"{args.key_id} revocada. Deja de servir en la siguiente petición.")
        return 0
    print(f"No hay ninguna clave activa con id {args.key_id}.", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m laplace_backend.keys", description="Claves de API de Laplace"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="crea una clave para un proyecto")
    c.add_argument("--project", required=True, help=f"id del proyecto, o '{ALL_PROJECTS}'")
    c.add_argument("--name", default="", help="para qué es, para reconocerla luego")
    c.set_defaults(func=crear)

    l = sub.add_parser("list", help="lista las claves (nunca la clave en claro)")  # noqa: E741
    l.add_argument("--project", default=None)
    l.set_defaults(func=listar)

    r = sub.add_parser("revoke", help="revoca una clave por su id")
    r.add_argument("key_id")
    r.set_defaults(func=revocar)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
