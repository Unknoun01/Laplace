"""Regenera la capa no verificada de precios a partir de la tabla de LiteLLM (D-138).

    python scripts/precios_litellm.py                     # descarga y escribe la capa
    python scripts/precios_litellm.py --desde tabla.json  # a partir de un fichero local
    python scripts/precios_litellm.py --informe informe.md

El informe compara las dos capas: dónde dice LiteLLM otra cosa que nuestra tabla
verificada (un aviso para reverificar, nunca un cambio automático de la verificada), y
qué modelos entran o salen de la capa. Es lo que el trabajo semanal de CI pone en la PR.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "apps" / "backend"))

from laplace_backend.pricing import (
    _PRICES_PATH,
    LITELLM_PATH,
    PriceTable,
    convertir_litellm,
)

URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

#: Por debajo de esta diferencia relativa no se avisa: son redondeos.
TOLERANCIA = 0.01

#: Los metros que se comparan entre las dos capas.
METROS = ("input", "output", "cached_input", "cache_write", "cache_write_1h")


def descargar(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as respuesta:
        return json.loads(respuesta.read().decode("utf-8"))


def discrepancias(
    nueva: dict[str, dict], tabla: PriceTable
) -> list[tuple[str, str, str, float, float]]:
    """`(modelo de LiteLLM, modelo verificado, metro, verificado, LiteLLM)` que no cuadran.

    `tabla` es la del producto, con las dos capas. Se mira todo modelo de LiteLLM que
    aun así se cobra con la verificada —él mismo o un snapshot suyo—, porque ahí es
    donde LiteLLM dice otra cosa y nosotros no le hacemos caso.
    """
    salida = []
    for nombre, precio in sorted(nueva.items()):
        propio = tabla.lookup(nombre)
        if propio is None or not propio.verified:
            continue
        for metro in METROS:
            suyo = precio.get(metro)
            nuestro = getattr(propio, metro)
            if suyo is None or nuestro is None:
                continue
            base = max(abs(nuestro), abs(suyo), 1e-12)
            if abs(nuestro - suyo) / base > TOLERANCIA:
                salida.append((nombre, propio.model, metro, nuestro, suyo))
    return salida


def informe(nueva: dict[str, dict], anterior: dict[str, dict], fecha: str) -> str:
    with tempfile.TemporaryDirectory() as carpeta:
        capa = Path(carpeta) / "capa.json"
        capa.write_text(json.dumps({"fetched_at": fecha, "models": nueva}), encoding="utf-8")
        tabla = PriceTable.load(_PRICES_PATH, unverified_path=capa)
    lineas = [
        f"## Capa de precios de LiteLLM, {fecha}",
        "",
        f"{len(nueva)} modelos de texto con tarifa (antes {len(anterior)}).",
        "",
    ]
    entran = sorted(set(nueva) - set(anterior))
    salen = sorted(set(anterior) - set(nueva))
    cambian = sorted(n for n in set(nueva) & set(anterior) if nueva[n] != anterior[n])
    lineas.append(
        f"Entran {len(entran)}, salen {len(salen)} y cambian de precio {len(cambian)}."
    )
    for titulo, grupo in (("Entran", entran), ("Salen", salen), ("Cambian", cambian)):
        if grupo:
            muestra = ", ".join(f"`{n}`" for n in grupo[:40])
            resto = f" y {len(grupo) - 40} más" if len(grupo) > 40 else ""
            lineas += ["", f"**{titulo}:** {muestra}{resto}."]

    choques = discrepancias(nueva, tabla)
    lineas += ["", "### Donde LiteLLM no coincide con nuestra tabla verificada", ""]
    if not choques:
        lineas.append("Ninguna diferencia por encima del 1 %.")
    else:
        lineas += [
            "No se cambia nada solo: la verificada sigue mandando. Cada fila es un motivo",
            "para volver a la página del proveedor y comprobar quién tiene razón.",
            "",
            "| Modelo en LiteLLM | Cobrado como | Metro | Nuestra tabla | LiteLLM |",
            "|---|---|---|---|---|",
        ]
        for nombre, propio, metro, nuestro, suyo in choques:
            lineas.append(f"| `{nombre}` | `{propio}` | {metro} | {nuestro:g} | {suyo:g} |")
    return "\n".join(lineas) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--desde", type=Path, help="tabla de LiteLLM ya descargada")
    parser.add_argument("--informe", type=Path, help="dónde escribir el informe en Markdown")
    parser.add_argument("--fecha", default=datetime.now(timezone.utc).date().isoformat())
    args = parser.parse_args()

    crudo = (
        json.loads(args.desde.read_text(encoding="utf-8")) if args.desde else descargar(URL)
    )
    nueva = convertir_litellm(crudo)
    try:
        anterior = json.loads(LITELLM_PATH.read_text(encoding="utf-8")).get("models", {})
    except FileNotFoundError:
        anterior = {}

    if args.informe:
        args.informe.write_text(informe(nueva, anterior, args.fecha), encoding="utf-8")

    if nueva == anterior:
        print(f"sin cambios: {len(nueva)} modelos")
        return 0
    LITELLM_PATH.write_text(
        json.dumps(
            {
                "_comment": (
                    "Capa NO verificada de precios, convertida de la tabla de LiteLLM a "
                    "USD por millón de tokens. No se edita a mano: la regenera "
                    "scripts/precios_litellm.py. La tabla verificada (model_prices.json) "
                    "manda siempre que resuelve un modelo."
                ),
                "url": URL,
                "fetched_at": args.fecha,
                "models": nueva,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"capa escrita: {len(nueva)} modelos (antes {len(anterior)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
