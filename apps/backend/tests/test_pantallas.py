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
import re
import socket
import subprocess
import sys
import time
import urllib.parse
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
    "clientes": ("/clientes/?project=demo&days=30", "iberviajes"),
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
        # Ningún texto se sale de su caja: un coste largo («0,003072 US$») se salía.
        sobran = pagina.evaluate(
            """() => [...document.querySelectorAll('section.grafo-agente g.nodo')]
                .map(g => [g.querySelector('rect').getBBox().width,
                           ...[...g.querySelectorAll('text')].map(t => t.getBBox().x
                              + t.getBBox().width)])
                .filter(([ancho, ...fines]) => fines.some(f => f > ancho))"""
        )
        assert sobran == [], sobran
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


def _texto_de(pagina, selector: str = "main") -> str:
    pagina.locator(selector).first.wait_for(timeout=15_000)
    return pagina.locator(selector).first.inner_text()


def test_el_heroe_dice_lo_ya_ahorrado(servidor, navegador):
    """D-156: la demo marca la repetición como arreglada; el seguimiento la verifica y
    el héroe dice lo que ya no se ha pagado, además de lo que se puede dejar de pagar."""
    pagina, errores = _abrir(navegador, servidor + "/?project=demo&days=30", "escritorio")
    try:
        pagina.wait_for_function(
            "() => document.querySelector('.hero .ahorrado')", timeout=15_000
        )
        ahorrado = pagina.locator(".hero .ahorrado").inner_text()
        assert "Ya has dejado de pagar" in ahorrado and "un problema" in ahorrado, ahorrado
        assert errores == [], errores
    finally:
        pagina.close()


def test_prompts_es_una_fuente_de_hallazgos(servidor, navegador):
    """D-157: la v2 de «atencion» cuesta más y sale en el Diagnóstico; su ficha lleva a
    Prompts y enseña el ciclo, y la ficha de Prompts avisa del problema abierto."""
    pagina, errores = _abrir(navegador, servidor + "/?project=demo&days=30", "escritorio")
    try:
        # El puesto de este problema depende de la hora a la que se cargó la demo: a
        # veces es el cuarto y va plegado en «ver más». Se despliega la lista entera.
        pagina.locator(".card").first.wait_for(timeout=15_000)
        pagina.evaluate("document.querySelectorAll('details.mas').forEach(d => d.open = true)")
        pagina.wait_for_function(
            "() => document.querySelector('main')?.innerText.includes('La v2 de «atencion»')",
            timeout=15_000,
        )
        pagina.get_by_text("La v2 de «atencion»").first.click()
        pagina.wait_for_function(
            "() => document.querySelector('.ciclo-pasos')", timeout=15_000
        )
        pasos = pagina.locator(".ciclo-paso")
        assert pasos.count() == 4
        assert "hecho" in (pasos.nth(0).get_attribute("class") or "")
        assert "Detectar" in pasos.nth(0).inner_text()
        assert "(pendiente)" in pasos.nth(2).inner_text()
        assert pagina.get_by_role("link", name="Abrir «atencion» en Prompts →").count() == 1
        assert errores == [], errores
    finally:
        pagina.close()

    pagina, errores = _abrir(navegador, servidor + "/prompts/?project=demo&days=30", "escritorio")
    try:
        pagina.wait_for_function(
            "() => document.querySelector('.prompt-hallazgo')", timeout=15_000
        )
        aviso = pagina.locator(".prompt-hallazgo").inner_text()
        assert "Sale en el Diagnóstico" in aviso and "v2" in aviso, aviso
        assert errores == [], errores
    finally:
        pagina.close()


def test_probar_empieza_por_los_arreglos_del_diagnostico(servidor, navegador):
    """D-156: la pestaña de Probar (antes Evaluaciones) lista los problemas abiertos
    con su dinero y si ya tienen prueba, antes que las tiradas."""
    pagina, errores = _abrir(navegador, servidor + "/evaluaciones/?project=demo&days=30",
                             "escritorio")
    try:
        pagina.wait_for_function(
            "() => document.querySelector('.por-probar li')", timeout=15_000
        )
        filas = pagina.locator(".por-probar li")
        assert filas.count() >= 3
        assert "sin probar" in filas.first.inner_text()
        texto = _texto_de(pagina)
        assert texto.index("Arreglos por probar") < texto.index("regresiones-atencion")
        assert errores == [], errores
    finally:
        pagina.close()


@pytest.mark.parametrize(
    ("ruta", "pestana"),
    [
        ("/?project=demo", "Diagnóstico"),
        ("/evaluaciones/?project=demo", "Probar"),
        ("/trazas/?project=demo", "Trazas"),
        ("/panel/?project=demo", "Panel"),
        ("/clientes/?project=demo", "Clientes"),
        ("/prompts/?project=demo", "Prompts"),
        ("/ajustes/?project=demo", "Ajustes"),
    ],
)
def test_la_pestana_de_cada_pantalla_sale_marcada(servidor, navegador, ruta, pestana):
    """Con la exportación estática las rutas llevan «/» al final, y las pestañas que
    comparaban la ruta exacta no se marcaban nunca."""
    pagina, _ = _abrir(navegador, servidor + ruta, "escritorio")
    try:
        pagina.locator("nav.nav a").first.wait_for(timeout=15_000)
        marcada = pagina.locator("nav.nav a[aria-current='page']")
        assert marcada.count() == 1
        assert marcada.inner_text() == pestana
        # Probar va justo después del Diagnóstico: es el paso siguiente del ciclo.
        orden = pagina.locator("nav.nav a").all_inner_texts()
        assert orden[:2] == ["Diagnóstico", "Probar"], orden
    finally:
        pagina.close()


@pytest.mark.parametrize(("tema", "fondo"), [(None, (10, 9, 8)), ("light", (255, 255, 255))])
def test_el_alto_contraste_quita_el_cristal(servidor, navegador, tema, fondo):
    """D-159: con alto contraste, el fondo es negro cálido o blanco puro y el cristal no
    desenfoca; el botón de Ajustes lo enciende y lo apaga sin recargar."""
    contexto = navegador.new_context(locale="es-ES", viewport={"width": 1440, "height": 900})
    try:
        if tema:
            contexto.add_init_script(f"localStorage.setItem('laplace.theme', '{tema}')")
        pagina = contexto.new_page()
        pagina.goto(servidor + "/ajustes/?project=demo", wait_until="networkidle")
        boton = pagina.get_by_role("button", name="Alto contraste")
        boton.wait_for(timeout=15_000)
        assert boton.get_attribute("aria-pressed") == "false"
        boton.click()
        assert pagina.evaluate("document.documentElement.dataset.contrast") == "high"
        pintado = pagina.evaluate(
            """() => [getComputedStyle(document.body).backgroundColor,
                      getComputedStyle(document.querySelector('.topbar')).backdropFilter]"""
        )
        assert pintado[0] == "rgb({}, {}, {})".format(*fondo), pintado
        assert pintado[1] in ("none", ""), pintado
        # Se recuerda al recargar, antes del primer pintado.
        pagina.reload(wait_until="networkidle")
        assert pagina.evaluate("document.documentElement.dataset.contrast") == "high"
        pagina.get_by_role("button", name="Alto contraste").click()
        assert pagina.evaluate("document.documentElement.dataset.contrast") is None
    finally:
        contexto.close()


def test_el_aviso_de_quien_hace_perder_dinero(servidor, navegador):
    """D-161: la demo tiene un cliente que cuesta más de lo que paga. La pestaña de
    Clientes lo dice lo primero, y el Diagnóstico lo avisa en su carril."""
    pagina, errores = _abrir(navegador, servidor + "/clientes/?project=demo&days=30",
                             "escritorio")
    try:
        pagina.wait_for_function("() => document.querySelector('.cl-aviso')", timeout=15_000)
        aviso = pagina.locator(".cl-aviso").inner_text()
        assert "Este cliente te hace perder dinero" in aviso and "iberviajes" in aviso, aviso
        texto = _texto_de(pagina)
        assert texto.index("te hace perder dinero") < texto.index("hoteles-mar")
        estados = pagina.locator(".cl-fila .chip").all_inner_texts()
        assert estados[0] == "pierde dinero" and "sin ingresos" in estados, estados
        assert errores == [], errores
    finally:
        pagina.close()

    pagina, errores = _abrir(navegador, servidor + "/?project=demo&days=30", "escritorio")
    try:
        pagina.wait_for_function(
            "() => document.querySelector('.clientes-aviso')", timeout=15_000
        )
        assert "1 cliente te hace perder dinero" in pagina.locator(".clientes-aviso").inner_text()
        assert errores == [], errores
    finally:
        pagina.close()


def test_del_cliente_a_sus_ejecuciones(servidor, navegador):
    """D-162: desde el aviso, «Ver sus ejecuciones» abre el explorador filtrado por ese
    cliente, y todas las filas son suyas (la sesión de la demo lleva el usuario, que es
    de iberviajes: u-01 a u-03)."""
    pagina, errores = _abrir(navegador, servidor + "/clientes/?project=demo&days=30",
                             "escritorio")
    try:
        pagina.wait_for_function("() => document.querySelector('.cl-aviso')", timeout=15_000)
        pagina.locator(".cl-aviso").get_by_role("link", name="Ver sus ejecuciones →").click()
        pagina.wait_for_function(
            "() => document.querySelector('main')?.innerText.includes('del cliente iberviajes')",
            timeout=15_000,
        )
        sesiones = pagina.locator("td").filter(has_text="sesión").all_inner_texts()
        assert sesiones and all(
            any(f"u-0{n}-" in s for n in (1, 2, 3)) for s in sesiones
        ), sesiones[:5]
        assert errores == [], errores
    finally:
        pagina.close()


def test_el_modelo_caro_se_prueba_con_replay(servidor, navegador):
    """D-167: con el modelo caro, la ficha no pide escribir código: da la orden de
    `laplace replay` con el modelo barato que propone el hallazgo."""
    import json as _json

    with urllib.request.urlopen(f"{servidor}/api/overview?project_id=demo&days=30") as r:
        hallazgos = _json.load(r)["findings"]
    caro = next(h for h in hallazgos if h["kind"] == "modelo_caro")
    pagina, errores = _abrir(
        navegador,
        f"{servidor}/problema/?project=demo&days=30&id={urllib.parse.quote(caro['id'])}",
        "escritorio",
    )
    try:
        pagina.get_by_role("button", name=re.compile("Crear el conjunto")).click()
        pagina.wait_for_function(
            "() => [...document.querySelectorAll('#probar pre')]"
            ".some(p => p.textContent.includes('laplace replay'))",
            timeout=15_000,
        )
        orden = pagina.locator("#probar pre").inner_text()
        assert "--modelo " in orden and "--tope " in orden and "--proyecto demo" in orden
        assert "run_dataset" not in orden
        assert "tu clave" in _texto_de(pagina, "#probar")
        assert errores == [], errores
    finally:
        pagina.close()


def test_al_cargar_no_se_pide_dos_veces_lo_mismo(servidor, navegador):
    """La barra, la página y el guardián de permisos pedían cada uno `/api/projects` y
    `/api/auth/me`: dos idas al servidor para la misma respuesta."""
    pagina = navegador.new_page(viewport={"width": 1440, "height": 900}, locale="es-ES")
    pedidas: list[str] = []
    pagina.on("request", lambda r: pedidas.append(urllib.parse.urlsplit(r.url).path))
    try:
        pagina.goto(servidor + "/?project=demo&days=7", wait_until="networkidle")
        for ruta in ("/api/projects", "/api/auth/me"):
            assert pedidas.count(ruta) <= 1, (ruta, pedidas.count(ruta))
    finally:
        pagina.close()


def test_ajustes_guarda_la_retencion_del_proyecto(servidor, navegador):
    """D-173: cada proyecto guarda sus días, y se puede borrar lo de una persona."""
    pagina, errores = _abrir(navegador, servidor + "/ajustes/?project=demo", "escritorio")
    try:
        campo = pagina.get_by_label("Días que guarda este proyecto")
        campo.wait_for(timeout=15_000)
        campo.fill("7")
        campo.locator("xpath=ancestor::div[contains(@class,'ab')]").get_by_role(
            "button", name="Guardar"
        ).click()
        pagina.wait_for_function(
            "() => document.querySelector('main').innerText.includes('guarda 7 días')",
            timeout=15_000,
        )
        boton = pagina.get_by_role("button", name="Borrar sus trazas")
        assert boton.is_disabled(), "sin repetir el id no se borra nada"
        assert errores == [], errores
    finally:
        pagina.close()
