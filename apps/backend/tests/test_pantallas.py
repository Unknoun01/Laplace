"""Las pantallas, abiertas en un navegador de verdad con la demo de un mes cargada.

Existe porque los fallos que más han dolido en este proyecto se veían en pantalla y en
ninguna aserción: un 404 que decía «enhorabuena» (D-113), una cifra que se leía
«$14 , 64», una tabla que se salía por la derecha en el móvil, un «cambio de prompt» que
no había ocurrido (D-134). La suite del backend no mira la pantalla; ésta sí.

Qué se exige en cada pantalla, a 1440 y a 375 px de ancho:

* que cargue sin errores en la consola ni excepciones de la página;
* que el documento no se salga por los lados (las tablas anchas llevan su propio scroll);
* que diga lo que tiene que decir con los datos de la demo delante.

Arranca `laplace ui` en un proceso aparte, contra un SQLite temporal, y le carga la demo
con `laplace demo`: el mismo camino que sigue quien lo instala. Se salta sola si no hay
Playwright o si la interfaz no está construida (`python scripts/build_ui.py`).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

RAIZ = Path(__file__).resolve().parents[3]
INTERFAZ = RAIZ / "apps" / "web" / "out" / "index.html"

pytestmark = pytest.mark.pantallas

ANCHOS = {"escritorio": (1440, 900), "movil": (375, 812)}

#: Cada pantalla y algo que tiene que decir con la demo cargada. Es poco a propósito:
#: lo que se comprueba aquí es que la pantalla se sostiene, no su redacción.
PANTALLAS = {
    "diagnostico": ("/?project=demo&days=7", "cosas que arreglar"),
    "trazas": ("/trazas/?project=demo&days=7", "Cada fila es una ejecución"),
    "panel": ("/panel/?project=demo&days=7", "por unidad de trabajo"),
    "evaluaciones": ("/evaluaciones/?project=demo&days=30", "regresiones-atencion"),
    "prompts": ("/prompts/?project=demo&days=30", "atencion"),
    "ajustes": ("/ajustes/?project=demo", "Presupuesto"),
}


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _esperar(url: str, segundos: float = 60) -> None:
    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"{url} no respondió en {segundos} s")


@pytest.fixture(scope="module")
def servidor(tmp_path_factory):
    if not INTERFAZ.exists():
        pytest.skip("la interfaz no está construida: python scripts/build_ui.py")
    casa = tmp_path_factory.mktemp("laplace")
    puerto = _puerto_libre()
    base = f"http://127.0.0.1:{puerto}"
    entorno = {**os.environ, "LAPLACE_HOME": str(casa), "LAPLACE_LOG_LEVEL": "warning"}
    proceso = subprocess.Popen(
        [sys.executable, "-m", "laplace.cli", "ui", "--port", str(puerto), "--no-browser"],
        env=entorno,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _esperar(f"{base}/health")
        demo = subprocess.run(
            [sys.executable, "-m", "laplace.cli", "demo", "--endpoint", base],
            env=entorno,
            capture_output=True,
            timeout=300,
            check=False,
        )
        assert demo.returncode == 0, demo.stderr.decode(errors="replace")
        yield base
    finally:
        proceso.terminate()
        try:
            proceso.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proceso.kill()


@pytest.fixture(scope="module")
def navegador():
    with playwright_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - sin navegador instalado no hay prueba
            pytest.skip(f"no hay Chromium para Playwright: {exc}")
        yield chromium
        chromium.close()


def _abrir(navegador, url: str, ancho: str):
    w, h = ANCHOS[ancho]
    pagina = navegador.new_page(viewport={"width": w, "height": h})
    errores: list[str] = []
    pagina.on("pageerror", lambda exc: errores.append(f"excepción: {exc}"))
    pagina.on(
        "console",
        lambda msg: errores.append(f"consola: {msg.text}") if msg.type == "error" else None,
    )
    pagina.goto(url, wait_until="networkidle")
    return pagina, errores


@pytest.mark.parametrize("ancho", ANCHOS)
@pytest.mark.parametrize("pantalla", PANTALLAS)
def test_la_pantalla_se_sostiene(servidor, navegador, pantalla, ancho):
    ruta, texto = PANTALLAS[pantalla]
    pagina, errores = _abrir(navegador, servidor + ruta, ancho)
    try:
        principal = pagina.locator("main")
        principal.wait_for(timeout=15_000)
        pagina.wait_for_function(
            "t => document.querySelector('main')?.innerText.includes(t)", arg=texto,
            timeout=15_000,
        )
        ancho_doc = pagina.evaluate("document.documentElement.scrollWidth")
        ancho_ventana = pagina.evaluate("window.innerWidth")
        assert ancho_doc <= ancho_ventana + 1, (
            f"{pantalla} a {ancho}: el documento mide {ancho_doc} px en una ventana de "
            f"{ancho_ventana}; lo ancho tiene que llevar su propio scroll"
        )
        assert errores == [], errores
    finally:
        pagina.close()


def test_una_traza_y_un_problema_se_abren(servidor, navegador):
    """Las dos pantallas a las que se llega desde las otras, por su enlace de verdad."""
    pagina, errores = _abrir(navegador, servidor + "/?project=demo&days=7", "escritorio")
    try:
        enlace = pagina.locator("a[href*='/problema']").first
        enlace.wait_for(timeout=15_000)
        titulo = enlace.inner_text()
        enlace.click()
        pagina.wait_for_function(
            "() => document.querySelector('main')?.innerText.includes('Cómo arreglarlo')",
            timeout=15_000,
        )
        assert "enhorabuena" not in pagina.locator("main").inner_text().lower(), titulo

        pagina.goto(servidor + "/trazas/?project=demo&days=7", wait_until="networkidle")
        fila = pagina.locator("a[href*='/traza?'], a[href*='/traza/?']").first
        fila.wait_for(timeout=15_000)
        fila.click()
        pagina.wait_for_function(
            "() => document.querySelector('main')?.innerText.includes('ÁRBOL DE EJECUCIÓN')"
            " || document.querySelector('main')?.innerText.includes('Árbol de ejecución')",
            timeout=15_000,
        )
        assert errores == [], errores
    finally:
        pagina.close()


def test_la_cifra_grande_no_se_parte(servidor, navegador):
    """«$14 , 64»: en una letra monoespaciada la coma ocupaba una celda entera (D-134)."""
    pagina, _ = _abrir(navegador, servidor + "/?project=demo&days=7", "movil")
    try:
        cifra = pagina.locator(".big").first
        cifra.wait_for(timeout=15_000)
        fuente = cifra.evaluate("e => getComputedStyle(e).fontFamily")
        assert "mono" not in fuente.lower(), fuente
    finally:
        pagina.close()
