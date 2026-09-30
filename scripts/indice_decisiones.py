"""Genera `docs/decisiones-indice.md`: las decisiones de `DECISIONS.md` por tema (D-174).

    python scripts/indice_decisiones.py            # lo escribe
    python scripts/indice_decisiones.py --comprobar # sale con 1 si está desfasado

`DECISIONS.md` va en orden de fecha y pasa de 250 KB: buscar «qué se decidió sobre la
caché» era leerlo entero. El índice agrupa cada decisión por tema según las palabras de
su título (una puede caer en varios) y deja al final la lista completa en orden. Se
genera, no se escribe a mano: `test_decisiones_indice.py` exige que esté al día.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ORIGEN = RAIZ / "DECISIONS.md"
DESTINO = RAIZ / "docs" / "decisiones-indice.md"

#: Tema → palabras (sin tildes, en minúsculas) que lo delatan en el título.
TEMAS: dict[str, tuple[str, ...]] = {
    "Coste, precios y caché": (
        "coste", "precio", "tarifa", "cache", "factura", "token", "dinero", "moneda",
        "euro", "ahorro", "gasto",
    ),
    "Detección: reglas y hallazgos": (
        "regla", "hallazgo", "repeti", "bucle", "modelo caro", "contexto fijo",
        "diagnostico", "derroche", "evitable", "doble", "desaparec", "paso",
    ),
    "Probar: evaluaciones, juez y replay": (
        "evaluaci", "juez", "tirada", "conjunto", "replay", "acierto", "a vs b", "probar",
        "veredicto", "casos", "calidad",
    ),
    "Prompts": ("prompt", "version"),
    "Instrumentación: SDK e integraciones": (
        "sdk", "integraci", "openinference", "openllmetry", "anthropic", "openai",
        "typescript", "node", "vercel", "langchain", "langgraph", "mastra", "streaming",
        "otlp", "ingesta", "instrument", "beta", "convencion",
    ),
    "Almacenes y escala": (
        "clickhouse", "sqlite", "postgres", "escala", "carga", "millones", "consulta",
        "paridad", "migra", "clave de ordenacion", "retencion", "final", "almacen",
    ),
    "Interfaz": (
        "interfaz", "pantalla", "tema", "claro", "oscuro", "contraste", "css", "cristal",
        "grafico", "grafo", "panel", "heroe", "movil", "barra", "ficha", "tarjeta",
        "pico", "en vivo", "url", "periodo anterior",
    ),
    "Alertas y presupuesto": ("alerta", "slack", "correo", "presupuesto", "aviso"),
    "Cuentas, claves y seguridad": (
        "cuenta", "clave", "autentic", "organizaci", "seguridad", "rol", "sesion",
        "invitaci", "health",
    ),
    "Margen por cliente": ("cliente", "margen", "stripe", "ingreso"),
    "Idiomas y textos": ("idioma", "texto", "espanol", "ingles", "traducc"),
    "Demo y datos de ejemplo": ("demo", "ejemplo"),
    "Datos, trazas y API": (
        "payload", "mensaje", "cursor", "lista de trazas", "ventana", "proyect", "api",
        "traza", "span", "stream_options", "flush", "indice",
    ),
    "Pruebas y método": (
        "prueba", "test", "auditor", "determin", "identificador", "alias", "helpers",
    ),
    "Proyecto y empaquetado": (
        "empaquet", "python", "licencia", "driver", "cli", "npm", "paquete", "extra",
        "monorepo", "dependencia", "local", "docker", "nube",
    ),
}

_DECISION = re.compile(r"^### (D-\d+[a-z]?) — (.+)$")


def _plano(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sin_tildes if not unicodedata.combining(c)).lower()


def _ancla(titulo: str) -> str:
    """El ancla que GitHub genera para un encabezado."""
    ancla = titulo.strip().lower()
    ancla = re.sub(r"[^\w\s-]", "", ancla, flags=re.UNICODE)
    return re.sub(r"\s", "-", ancla)


def decisiones(texto: str) -> list[tuple[str, str, str]]:
    """(id, título, sección de fecha) de cada decisión, en orden."""
    salida, seccion = [], ""
    for linea in texto.splitlines():
        if linea.startswith("## "):
            seccion = linea[3:].strip()
        encontrada = _DECISION.match(linea)
        if encontrada:
            salida.append((encontrada.group(1), encontrada.group(2).strip(), seccion))
    return salida


def generar(texto: str) -> str:
    todas = decisiones(texto)
    lineas = [
        "# Índice de decisiones",
        "",
        "Generado por `scripts/indice_decisiones.py` a partir de `DECISIONS.md`: no se edita a",
        "mano. Cada decisión cae en los temas cuyas palabras aparecen en su título, así que",
        "una puede salir en varios, y alguna en ninguno: la lista completa, en orden, está al",
        f"final. {len(todas)} decisiones.",
        "",
    ]

    def enlace(did: str, titulo: str) -> str:
        return f"- [{did}](../DECISIONS.md#{_ancla(f'{did} — {titulo}')}) — {titulo}"

    for tema, palabras in TEMAS.items():
        del_tema = [d for d in todas if any(p in _plano(d[1]) for p in palabras)]
        if not del_tema:
            continue
        lineas += [f"## {tema}", ""]
        lineas += [enlace(did, titulo) for did, titulo, _ in del_tema]
        lineas.append("")
    lineas += ["## Todas, en orden", ""]
    seccion = None
    for did, titulo, de in todas:
        if de != seccion:
            lineas += ["", f"**{de}**", ""] if seccion is not None else [f"**{de}**", ""]
            seccion = de
        lineas.append(enlace(did, titulo))
    return "\n".join(lineas) + "\n"


def main(argv: list[str]) -> int:
    nuevo = generar(ORIGEN.read_text(encoding="utf-8"))
    if "--comprobar" in argv:
        actual = DESTINO.read_text(encoding="utf-8") if DESTINO.exists() else ""
        if actual != nuevo:
            print("docs/decisiones-indice.md está desfasado: python scripts/indice_decisiones.py")
            return 1
        return 0
    DESTINO.write_text(nuevo, encoding="utf-8")
    print(f"escrito {DESTINO.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
