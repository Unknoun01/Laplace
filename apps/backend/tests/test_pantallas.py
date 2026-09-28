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
    # En español a propósito: Chromium sin interfaz anuncia `en-US`, y los textos que se
    # buscan abajo son los del catálogo de origen (D-147).
    pagina = navegador.new_page(viewport={"width": w, "height": h}, locale="es-ES")
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


def test_buscar_en_el_contenido_y_pasar_de_pagina(servidor, navegador):
    """La pregunta del usuario no es el nombre de ningún paso: por nombre no sale nada y
    en el contenido sí (D-144). Y la página siguiente conserva la búsqueda, que antes se
    perdía con todos los demás filtros."""
    base = servidor + "/trazas/?project=demo&days=30&sort=recent&q=GUITARRA"
    pagina, errores = _abrir(navegador, base, "escritorio")
    try:
        pagina.wait_for_function(
            "() => document.querySelector('main')?.innerText.includes('Ninguna traza coincide')",
            timeout=15_000,
        )
        pagina.goto(base + "&en=contenido", wait_until="networkidle")
        fila = pagina.locator("a[href*='/traza?'], a[href*='/traza/?']").first
        fila.wait_for(timeout=15_000)
        # En la demo la pregunta de la guitarra sale en más de 50 ejecuciones del mes.
        href = pagina.locator("a", has_text="Más antiguas").first.get_attribute("href") or ""
        assert "en=contenido" in href and "q=GUITARRA" in href, href
        assert errores == [], errores
    finally:
        pagina.close()


def test_el_idioma_sale_del_navegador_y_el_selector_lo_cambia(servidor, navegador):
    """Quien abre Laplace con el navegador en francés lo ve en francés, con las cifras
    como se escriben en francés; y lo que elija en el selector se queda (D-147)."""
    contexto = navegador.new_context(locale="fr-FR", viewport={"width": 1440, "height": 900})
    pagina = contexto.new_page()
    errores: list[str] = []
    pagina.on("pageerror", lambda exc: errores.append(str(exc)))
    try:
        pagina.goto(servidor + "/panel/?project=demo&days=30", wait_until="networkidle")
        nav = pagina.locator("nav.nav")
        nav.get_by_text("Tableau de bord").wait_for(timeout=15_000)
        assert pagina.evaluate("document.documentElement.lang") == "fr-FR"
        pagina.wait_for_function(
            "() => document.querySelector('.mgrid .mcard b')?.textContent.includes('$US')",
            timeout=15_000,
        )

        pagina.locator("select.idioma").select_option("en")
        nav.get_by_text("Dashboard").wait_for(timeout=15_000)
        pagina.reload(wait_until="networkidle")
        nav.get_by_text("Dashboard").wait_for(timeout=15_000)
        assert errores == [], errores
    finally:
        contexto.close()


#: Palabras de la interfaz en español que no salen en los datos de la demo (que sí están
#: en español: son las instrucciones y las preguntas de su agente).
ESPANOL_DE_INTERFAZ = ("ejecución", "ejecuciones", "presupuesto", "arreglar", "guardar", "coste")


@pytest.mark.parametrize("pantalla", ["diagnostico", "trazas", "panel", "evaluaciones", "prompts"])
def test_en_ingles_no_queda_interfaz_en_espanol(servidor, navegador, pantalla):
    """Una frase escrita a mano en un componente sale en español en cualquier idioma, y
    ninguna prueba en español lo nota (D-148)."""
    ruta, _ = PANTALLAS[pantalla]
    contexto = navegador.new_context(locale="en-US", viewport={"width": 1440, "height": 900})
    pagina = contexto.new_page()
    try:
        pagina.goto(servidor + ruta, wait_until="networkidle")
        pagina.locator("main").wait_for(timeout=15_000)
        pagina.wait_for_function("() => !document.querySelector('main .sk')", timeout=15_000)
        texto = pagina.locator("main").inner_text().lower()
        restos = [p for p in ESPANOL_DE_INTERFAZ if p in texto]
        assert restos == [], (pantalla, restos)
    finally:
        contexto.close()


def _traza_de_hace(servidor: str, proyecto: str, dias: float) -> None:
    """Una traza de `proyecto` fechada hace `dias`, por OTLP/JSON como la mandaría Node."""
    import json

    fin = time.time_ns() - int(dias * 86_400 * 1e9)
    span = {
        "traceId": os.urandom(16).hex(),
        "spanId": os.urandom(8).hex(),
        "name": "atender",
        "kind": 1,
        "startTimeUnixNano": str(fin - 2_000_000_000),
        "endTimeUnixNano": str(fin),
        "status": {"code": 1},
    }
    cuerpo = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": proyecto}}]
                },
                "scopeSpans": [{"scope": {"name": "prueba"}, "spans": [span]}],
            }
        ]
    }
    peticion = urllib.request.Request(
        servidor + "/v1/traces",
        data=json.dumps(cuerpo).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(peticion, timeout=10):
        pass


@pytest.mark.parametrize("pantalla", ["/", "/panel/"])
def test_un_rango_vacio_no_dice_que_no_ha_llegado_nada(servidor, navegador, pantalla):
    """A1 de la auditoría del rediseño: con trazas de hace tres días y el rango en un
    día, la pantalla decía «Esperando la primera ejecución» y hacía pensar que la
    instalación no funcionaba. Ahora dice cuándo llegó la última y ofrece el rango que
    la incluye, que al pulsarlo la enseña."""
    _traza_de_hace(servidor, "antiguo", 3)
    url = f"{servidor}{pantalla}?project=antiguo&days=1"
    pagina, errores = _abrir(navegador, url, "escritorio")
    try:
        pagina.wait_for_function(
            "() => document.body.innerText.includes('No hay ejecuciones')",
            timeout=15_000,
        )
        texto = pagina.locator("body").inner_text()
        assert "Esperando la primera ejecución" not in texto
        assert "La última llegó el" in texto
        pagina.locator("a", has_text="Ver los últimos 7 días").click()
        pagina.wait_for_function(
            "() => !document.body.innerText.includes('No hay ejecuciones')",
            timeout=15_000,
        )
        assert "days=7" in pagina.url and "project=antiguo" in pagina.url
        assert errores == [], errores
    finally:
        pagina.close()


def test_una_traza_mas_vieja_que_cualquier_rango_no_ofrece_boton(servidor, navegador):
    """Si la última es de antes del rango más largo, no hay rango que ofrecer: se dice
    la fecha y que hace falta tráfico nuevo, sin un botón que no llevaría a nada."""
    _traza_de_hace(servidor, "vetusto", 90)
    pagina, errores = _abrir(navegador, servidor + "/?project=vetusto&days=30", "escritorio")
    try:
        pagina.wait_for_function(
            "() => document.body.innerText.includes('No hay ejecuciones')",
            timeout=15_000,
        )
        texto = pagina.locator("body").inner_text()
        assert "antes de los 30 días" in texto, texto
        assert pagina.locator("a", has_text="Ver los últimos").count() == 0
        assert errores == [], errores
    finally:
        pagina.close()


def test_en_movil_el_coste_de_cada_traza_se_ve_sin_desplazar(servidor, navegador):
    """A2 de la auditoría: a 375 px la tabla de trazas medía 479 y del coste —la columna
    por la que se ordena— sólo asomaba el símbolo. Ahora cabe en su marco."""
    pagina, errores = _abrir(navegador, servidor + "/trazas/?project=demo&days=7", "movil")
    try:
        pagina.locator("td.money").first.wait_for(timeout=15_000)
        medidas = pagina.evaluate(
            """() => {
                const marco = document.querySelector('.tbl-scroll');
                const coste = document.querySelector('td.money').getBoundingClientRect();
                return {tabla: marco.scrollWidth, marco: marco.clientWidth,
                        derecha: coste.right, borde: marco.getBoundingClientRect().right};
            }"""
        )
        assert medidas["tabla"] <= medidas["marco"] + 1, medidas
        assert medidas["derecha"] <= medidas["borde"], medidas
        assert errores == [], errores
    finally:
        pagina.close()


@pytest.mark.parametrize(("ancho", "alto"), [(375, 812), (1024, 768), (1440, 900)])
@pytest.mark.parametrize("lengua", ["es-ES", "fr-FR"])
def test_las_seis_pestanas_se_ven_enteras(servidor, navegador, ancho, alto, lengua):
    """B1 y la navegación en móvil: a 1024 px «Ajustes» quedaba bajo el difuminado, y a
    375 «Prompts» y «Ajustes» detrás del borde de una barra desplazable. Cada pestaña
    tiene que caber entera dentro de la barra, sin desplazarla."""
    pagina = navegador.new_page(viewport={"width": ancho, "height": alto}, locale=lengua)
    try:
        pagina.goto(servidor + "/?project=demo&days=7", wait_until="networkidle")
        pagina.locator("nav.nav a").first.wait_for(timeout=15_000)
        fuera = pagina.evaluate(
            """() => {
                const nav = document.querySelector('nav.nav');
                const caja = nav.getBoundingClientRect();
                return [...nav.querySelectorAll('a')]
                    .filter(a => {
                        const r = a.getBoundingClientRect();
                        return r.left < caja.left - 1 || r.right > caja.right + 1
                            || a.scrollWidth > a.clientWidth + 1;
                    })
                    .map(a => a.innerText);
            }"""
        )
        assert fuera == [], f"pestañas que no se ven enteras a {ancho} px: {fuera}"
        assert pagina.locator("nav.nav").get_attribute("data-desborda") in ("", None)
    finally:
        pagina.close()


@pytest.mark.parametrize("ruta", ["/?project=demo&days=7", "/panel/?project=demo&days=7"])
def test_a_1440_el_contenido_no_deja_un_tercio_vacio(servidor, navegador, ruta):
    """A 1440 px el contenido acababa en 980 (Panel) o 1200 (Diagnóstico) con la
    cabecera hasta 1420: un tercio de pantalla vacío a la derecha. Ahora el contenido
    llega hasta donde llega la cabecera."""
    pagina, errores = _abrir(navegador, servidor + ruta, "escritorio")
    try:
        pagina.locator("main").wait_for(timeout=15_000)
        pagina.wait_for_timeout(500)
        medidas = pagina.evaluate(
            """() => ({main: document.querySelector('main').getBoundingClientRect().toJSON(),
                      barra: document.querySelector('.topbar').getBoundingClientRect().toJSON()})"""
        )
        assert abs(medidas["main"]["left"] - medidas["barra"]["left"]) <= 1, medidas
        assert medidas["barra"]["right"] - medidas["main"]["right"] <= 2, medidas
        assert errores == [], errores
    finally:
        pagina.close()


def test_el_heroe_dice_lo_que_puedes_dejar_de_pagar(servidor, navegador):
    """Menos texto (D-151): la cifra grande es lo que se puede dejar de pagar, y las
    salvedades van plegadas en un distintivo de confianza que se abre con un clic."""
    pagina, errores = _abrir(navegador, servidor + "/?project=demo&days=7", "escritorio")
    try:
        pagina.locator(".big.save").wait_for(timeout=15_000)
        heroe = pagina.locator("section.hero")
        assert "Puedes dejar de pagar hasta" in heroe.inner_text()
        # Plegado: la línea de la proyección no se ve hasta abrir el distintivo.
        distintivo = pagina.locator("details.confianza")
        assert "Confianza" in distintivo.locator(":scope > summary").inner_text()
        assert not pagina.get_by_text("Proyección desde").first.is_visible()
        distintivo.locator(":scope > summary").click()
        assert pagina.get_by_text("Proyección desde").first.is_visible()
        assert errores == [], errores
    finally:
        pagina.close()


def test_la_ficha_pliega_el_porque(servidor, navegador):
    """Una frase arriba y el «¿Por qué pasa?» plegado: se lee qué pasa y cómo arreglarlo
    sin tres párrafos en medio."""
    pagina, errores = _abrir(navegador, servidor + "/?project=demo&days=7", "escritorio")
    try:
        enlace = pagina.locator("a[href*='/problema']").first
        enlace.wait_for(timeout=15_000)
        enlace.click()
        porque = pagina.locator("details.porque-bloque")
        porque.wait_for(timeout=15_000)
        assert porque.get_attribute("open") is None
        assert "¿Por qué pasa?" in porque.locator("summary").inner_text()
        assert errores == [], errores
    finally:
        pagina.close()


def test_el_diagnostico_ensena_donde_se_va_el_dinero(servidor, navegador):
    """D-152: gasto por día con la franja evitable, y coste por paso sin dos filas con
    el mismo nombre (en la demo salían tres «responder»)."""
    pagina, errores = _abrir(navegador, servidor + "/?project=demo&days=7", "escritorio")
    try:
        graficos = pagina.locator("section.graficos")
        graficos.wait_for(timeout=15_000)
        assert graficos.locator("rect.evitable").count() > 0
        nombres = graficos.locator(".pasos-coste .nombre").all_inner_texts()
        assert nombres and len(nombres) == len(set(nombres)), nombres
        assert errores == [], errores
    finally:
        pagina.close()


def test_el_css_tambien_habla_el_idioma_elegido(servidor, navegador):
    """El «¿por qué?» de un aviso plegado lo pinta el CSS; en inglés dice «why?»."""
    pagina = navegador.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
    try:
        pagina.goto(servidor + "/?project=demo&days=7", wait_until="networkidle")
        pagina.locator("details.confianza").wait_for(timeout=15_000)
        sufijo = pagina.evaluate(
            """() => getComputedStyle(
                document.querySelector('details.porque:not(.pregunta) > summary'), '::after'
            ).content"""
        )
        assert "why" in sufijo and "qué" not in sufijo, sufijo
    finally:
        pagina.close()


def test_la_traza_ensena_el_grafo_del_agente(servidor, navegador):
    """D-153: una caja por paso y una flecha por llamada, con cuántas veces. En la demo
    las trazas con bucle llaman a «consultar_manual» varias veces desde el mismo paso."""
    base = servidor + "/trazas/?project=demo&days=30&sort=cost"
    pagina, errores = _abrir(navegador, base, "escritorio")
    try:
        fila = pagina.locator("a[href*='/traza?'], a[href*='/traza/?']").first
        fila.wait_for(timeout=15_000)
        fila.click()
        grafo = pagina.locator("section.grafo-agente")
        grafo.wait_for(timeout=15_000)
        assert grafo.locator("g.nodo").count() >= 2
        assert grafo.locator("g.arista").count() >= 1
        # Una traza de la demo con bucle: «×n» en alguna flecha.
        pagina.goto(servidor + "/trazas/?project=demo&days=30&sort=cost&q=consultar_manual",
                    wait_until="networkidle")
        pagina.locator("a[href*='/traza?'], a[href*='/traza/?']").first.click()
        pagina.locator("section.grafo-agente").wait_for(timeout=15_000)
        assert pagina.locator("section.grafo-agente g.arista.varias").count() >= 1
        assert errores == [], errores
    finally:
        pagina.close()


@pytest.mark.parametrize(
    ("guardado", "sistema", "oscuro"),
    [
        (None, "light", True),
        ("light", "dark", False),
        ("dark", "light", True),
        ("system", "light", False),
        ("system", "dark", True),
    ],
)
def test_el_tema_oscuro_es_el_de_partida(servidor, navegador, guardado, sistema, oscuro):
    """D-155: sin nada elegido se ve el oscuro, aunque el sistema esté en claro; el claro
    entra por elección o por «como el sistema». Se mide el fondo pintado, no la clase."""
    contexto = navegador.new_context(color_scheme=sistema, locale="es-ES")
    try:
        if guardado:
            contexto.add_init_script(f"localStorage.setItem('laplace.theme', '{guardado}')")
        pagina = contexto.new_page()
        pagina.goto(servidor + "/ajustes/?project=demo", wait_until="networkidle")
        pagina.locator("main").wait_for(timeout=15_000)
        luz = pagina.evaluate(
            """() => {
                const [r, g, b] = getComputedStyle(document.body).backgroundColor
                    .match(/[\\d.]+/g).map(Number);
                return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
            }"""
        )
        assert (luz < 0.2) is oscuro, (guardado, sistema, luz)
        # El selector de Ajustes marca lo que se ve.
        marcado = {None: "Oscuro", "dark": "Oscuro", "light": "Claro",
                   "system": "Como el sistema"}[guardado]
        boton = pagina.get_by_role("button", name=marcado, exact=True)
        assert boton.get_attribute("aria-pressed") == "true"
    finally:
        contexto.close()
