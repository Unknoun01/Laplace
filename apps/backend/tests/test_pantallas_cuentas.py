"""Las pantallas con sesión, de punta a punta en un navegador (D-175).

La auditoría del rediseño (`docs/auditoria-rediseno.md`) no llegó a las pantallas que
sólo existen con cuentas: configurar la instalación, entrar, la organización con sus
invitaciones y claves, y aceptar una invitación. `test_cuentas.py` prueba sus rutas por
la API; aquí se recorren como lo hace una persona, escribiendo en los formularios, y se
exige lo mismo que a las demás pantallas en `test_pantallas.py`: sin errores en la
consola y sin salirse por los lados a 1440 ni a 375 px.

Corre `laplace ui` con `LAPLACE_AUTH_REQUIRED=true`: la instalación «de nube» sobre
SQLite, como `test_cuentas.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_pantallas import (  # noqa: E402
    ANCHOS,
    INTERFAZ,
    _esperar,
    _puerto_libre,
    navegador,  # noqa: F401 - fixture
)

pytestmark = pytest.mark.pantallas

CODIGO = "codigo-de-prueba-123"
CONTRASENA = "una-contraseña-larga"


@pytest.fixture(scope="module")
def servidor(tmp_path_factory):
    if not INTERFAZ.exists():
        pytest.skip("la interfaz no está construida: python scripts/build_ui.py")
    casa = tmp_path_factory.mktemp("laplace")
    puerto = _puerto_libre()
    base = f"http://127.0.0.1:{puerto}"
    entorno = {
        **os.environ,
        "LAPLACE_HOME": str(casa),
        "LAPLACE_LOG_LEVEL": "warning",
        "LAPLACE_AUTH_REQUIRED": "true",
        "LAPLACE_SETUP_TOKEN": CODIGO,
    }
    proceso = subprocess.Popen(
        [sys.executable, "-m", "laplace.cli", "ui", "--port", str(puerto), "--no-browser"],
        env=entorno,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _esperar(f"{base}/health")
        yield base
    finally:
        proceso.terminate()
        try:
            proceso.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proceso.kill()


def _contexto(navegador, ancho: str = "escritorio"):  # noqa: F811
    w, h = ANCHOS[ancho]
    contexto = navegador.new_context(viewport={"width": w, "height": h}, locale="es-ES")
    errores: list[str] = []

    def vigilar(pagina):
        pagina.on("pageerror", lambda exc: errores.append(f"excepción: {exc}"))
        pagina.on(
            "console",
            lambda m: errores.append(f"consola: {m.text}") if m.type == "error" else None,
        )

    contexto.on("page", vigilar)
    return contexto, errores


def _espera_texto(pagina, texto: str) -> None:
    pagina.wait_for_function(
        "t => document.querySelector('main')?.innerText.includes(t)", arg=texto,
        timeout=15_000,
    )


def _no_se_sale(pagina, donde: str) -> None:
    ancho_doc = pagina.evaluate("document.documentElement.scrollWidth")
    ventana = pagina.evaluate("window.innerWidth")
    if ancho_doc <= ventana + 1:
        return
    # Qué se sale: el más interno de los que pasan del borde, para no buscarlo a mano.
    culpables = pagina.evaluate(
        """w => [...document.querySelectorAll('main *')]
            .filter(e => e.getBoundingClientRect().right > w + 1)
            .filter(e => ![...e.children].some(c => c.getBoundingClientRect().right > w + 1))
            .slice(0, 5)
            .map(e => `${e.tagName.toLowerCase()}.${e.className} → ${
                Math.round(e.getBoundingClientRect().right)}`)""",
        ventana,
    )
    raise AssertionError(f"{donde}: {ancho_doc} px en una ventana de {ventana}: {culpables}")


def test_de_la_instalacion_vacia_a_un_equipo_con_clave(servidor, navegador):  # noqa: F811
    """El recorrido entero, en orden, porque cada paso necesita el anterior."""
    admin, errores_admin = _contexto(navegador)
    try:
        pagina = admin.new_page()
        # 1. Sin cuentas todavía, entrar lleva a configurar.
        pagina.goto(servidor + "/entrar", wait_until="networkidle")
        pagina.wait_for_url("**/configurar**", timeout=15_000)
        campos = pagina.locator("form.auth-card input")
        campos.nth(0).fill(CODIGO)
        campos.nth(1).fill("Viajes Sol")
        campos.nth(2).fill("Ana")
        campos.nth(3).fill("ana@ejemplo.com")
        campos.nth(4).fill(CONTRASENA)
        pagina.locator("form.auth-card button[type=submit]").click()

        # 2. La organización recién creada, con quien la creó como propietaria.
        pagina.wait_for_url("**/organizacion**", timeout=15_000)
        _espera_texto(pagina, "Viajes Sol")
        _espera_texto(pagina, "ana@ejemplo.com")

        # 3. Una clave para un proyecto nuevo: se enseña una vez, con el código de init.
        claves = pagina.locator("section.sec", has=pagina.locator("input[list=proyectos-org]"))
        claves.locator("input[list=proyectos-org]").fill("agente-reservas")
        claves.locator("button.btn.primary").click()
        clave = claves.locator(".una-vez .copiable code").inner_text(timeout=15_000)
        assert clave.startswith("lp_"), clave
        assert 'project="agente-reservas"' in claves.locator(".una-vez pre").inner_text()
        claves.locator("td", has_text="agente-reservas").first.wait_for(timeout=15_000)

        # 4. Invitar a alguien: sin correo configurado, el enlace se enseña para pasarlo.
        invitar = pagina.locator("section.sec", has=pagina.locator("input[type=email]"))
        invitar.locator("input[type=email]").fill("luis@ejemplo.com")
        invitar.locator("button.btn.primary").click()
        enlace = invitar.locator(".una-vez .copiable code").inner_text(timeout=15_000)
        assert "/invitacion?token=" in enlace or "/invitacion/?token=" in enlace, enlace
        invitar.locator("li", has_text="luis@ejemplo.com").wait_for(timeout=15_000)
        _no_se_sale(pagina, "organización a 1440")
        pagina.set_viewport_size({"width": 375, "height": 812})
        _no_se_sale(pagina, "organización a 375")
        assert errores_admin == [], errores_admin
    finally:
        admin.close()

    # 5. Quien recibe el enlace lo abre en su navegador, elige contraseña y entra.
    invitado, errores_inv = _contexto(navegador, "movil")
    try:
        pagina = invitado.new_page()
        pagina.goto(enlace, wait_until="networkidle")
        _espera_texto(pagina, "Viajes Sol")
        _no_se_sale(pagina, "invitación a 375")
        campos = pagina.locator("form.auth-card input:not([disabled])")
        campos.nth(0).fill("Luis")
        campos.nth(1).fill(CONTRASENA)
        pagina.locator("form.auth-card button[type=submit]").click()
        pagina.wait_for_url(lambda u: "/invitacion" not in u, timeout=15_000)
        pagina.goto(servidor + "/organizacion", wait_until="networkidle")
        _espera_texto(pagina, "luis@ejemplo.com")
        texto = pagina.locator("main").inner_text()
        # Un miembro no ve lo que no puede hacer (ni invitar ni claves).
        assert pagina.locator("input[list=proyectos-org]").count() == 0, texto
        assert pagina.locator("input[type=email]").count() == 0, texto
        _no_se_sale(pagina, "organización de un miembro a 375")
        assert errores_inv == [], errores_inv
    finally:
        invitado.close()

    # 6. Entrar: la contraseña mala da el error de siempre; la buena, a donde se iba.
    otra, errores_otra = _contexto(navegador, "movil")
    try:
        pagina = otra.new_page()
        pagina.goto(servidor + "/entrar?next=/organizacion", wait_until="networkidle")
        campos = pagina.locator("form.auth-card input[type=email], form.auth-card "
                                "input[autocomplete=current-password]")
        campos.nth(0).fill("ana@ejemplo.com")
        campos.nth(1).fill("no-es-esta-contraseña")
        pagina.locator("form.auth-card button[type=submit]").click()
        pagina.locator("form.auth-card .verr").wait_for(timeout=15_000)
        _no_se_sale(pagina, "entrar a 375")
        campos.nth(1).fill(CONTRASENA)
        pagina.locator("form.auth-card button[type=submit]").click()
        pagina.wait_for_url("**/organizacion**", timeout=15_000)
        _espera_texto(pagina, "Viajes Sol")
        # El 401 de la contraseña mala es la respuesta esperada, no un fallo de la página.
        assert [e for e in errores_otra if "401" not in e] == [], errores_otra
    finally:
        otra.close()

    # 7. Entrar con la clave del proyecto, sin cuenta: se llega a ese proyecto, que aún
    #    no ha mandado nada. Antes decía «todavía no hay ningún proyecto» (D-175).
    con_clave, errores_clave = _contexto(navegador)
    try:
        pagina = con_clave.new_page()
        pagina.goto(servidor + "/entrar", wait_until="networkidle")
        pagina.locator("details.con-clave summary").click()
        pagina.locator("details.con-clave input").fill(clave)
        pagina.locator("details.con-clave button").click()
        pagina.wait_for_url(lambda u: "/entrar" not in u, timeout=15_000)
        # Los estados vacíos no van dentro de `main`: se mira el cuerpo entero.
        pagina.wait_for_function(
            "() => document.body.innerText.includes('«agente-reservas»')", timeout=15_000
        )
        ejemplo = pagina.locator("pre").inner_text()
        assert 'project="agente-reservas"' in ejemplo, ejemplo
        assert "api_key=" in ejemplo, "con cuentas, un init sin clave da 401"
        assert errores_clave == [], errores_clave
    finally:
        con_clave.close()
