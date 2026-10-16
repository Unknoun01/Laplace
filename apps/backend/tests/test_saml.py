"""Entrar por SAML (D-211). Casi todo son ataques que no deben entrar:
- la aserción sin firmar, o firmada con otro certificado, o con SHA-1;
- la respuesta firmada con la aserción sin firmar;
- la aserción envuelta (*signature wrapping*), de lado y por dentro;
- otra audiencia, sin audiencia, otro destino, otro emisor;
- caducada, repetida, o respuesta a una petición que no es la de este navegador;
- un correo fuera de los dominios de la organización;
- y sin `xmlsec`, SAML apagado: nunca se entra sin validar la firma.

El proveedor es falso, pero firma de verdad con `xmlsec` y un certificado de la prueba.
Sin `xmlsec` se saltan, salvo la de SAML apagado.
"""

from __future__ import annotations

import base64
import copy
import datetime as dt
import re
import time
import urllib.parse
import uuid
import zlib

import pytest
from test_cuentas import H, _cliente, _configurar, app  # noqa: F401
from test_empresa import _org

from laplace_backend import saml

con_xmlsec = pytest.mark.skipif(not saml.disponible(), reason="sin xmlsec (pip install .[saml])")

IDP = "https://idp.ejemplo.com/saml/metadata"
IDP_SSO = "https://idp.ejemplo.com/saml/sso"
#: La URL pública de la instalación. `testserver` no le vale a python3-saml como host.
BASE = "http://localhost:8000"
SP = f"{BASE}/api/auth/sso/saml"
ACS = f"{BASE}/api/auth/sso/saml/acs"
NS = {"samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
      "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
      "ds": "http://www.w3.org/2000/09/xmldsig#"}


def _certificado(nombre: str) -> tuple[bytes, bytes]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nombre)])
    ahora = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(sujeto).issuer_name(sujeto)
        .public_key(clave.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(ahora - dt.timedelta(days=1))
        .not_valid_after(ahora + dt.timedelta(days=30))
        .sign(clave, hashes.SHA256())
    )
    return (
        clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()),
        cert.public_bytes(serialization.Encoding.PEM),
    )


@pytest.fixture(scope="module")
def certs():
    if not saml.disponible():
        pytest.skip("sin xmlsec")
    return {"bueno": _certificado("idp"), "otro": _certificado("atacante")}


def _hora(desplazamiento: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + desplazamiento))


def _respuesta(request_id: str, *, email="luis@ejemplo.com", nameid="luis-idp",
               audiencia: str | None = SP, destino=ACS, emisor=IDP, vence=300.0) -> str:
    aid = f"_a{uuid.uuid4().hex}"
    restriccion = (f"<saml:AudienceRestriction><saml:Audience>{audiencia}</saml:Audience>"
                   "</saml:AudienceRestriction>") if audiencia else ""
    return f"""<samlp:Response xmlns:samlp="{NS['samlp']}" xmlns:saml="{NS['saml']}" \
ID="_r{uuid.uuid4().hex}" Version="2.0" IssueInstant="{_hora(vence - 300)}" \
Destination="{destino}" InResponseTo="{request_id}">\
<saml:Issuer>{emisor}</saml:Issuer>\
<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>\
<saml:Assertion ID="{aid}" Version="2.0" IssueInstant="{_hora(vence - 300)}">\
<saml:Issuer>{emisor}</saml:Issuer>\
<saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent">\
{nameid}</saml:NameID><saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">\
<saml:SubjectConfirmationData NotOnOrAfter="{_hora(vence)}" Recipient="{destino}" \
InResponseTo="{request_id}"/></saml:SubjectConfirmation></saml:Subject>\
<saml:Conditions NotBefore="{_hora(vence - 360)}" NotOnOrAfter="{_hora(vence)}">{restriccion}\
</saml:Conditions>\
<saml:AuthnStatement AuthnInstant="{_hora(vence - 300)}" SessionIndex="{aid}"><saml:AuthnContext>\
<saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport\
</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement>\
<saml:AttributeStatement><saml:Attribute Name="email"><saml:AttributeValue>{email}\
</saml:AttributeValue></saml:Attribute><saml:Attribute Name="displayName"><saml:AttributeValue>\
Luis</saml:AttributeValue></saml:Attribute></saml:AttributeStatement>\
</saml:Assertion></samlp:Response>"""


def _firmar(elemento, par: tuple[bytes, bytes], sha1: bool = False) -> None:
    import xmlsec

    algoritmo = xmlsec.Transform.RSA_SHA1 if sha1 else xmlsec.Transform.RSA_SHA256
    firma = xmlsec.template.create(elemento, xmlsec.Transform.EXCL_C14N, algoritmo, ns="ds")
    elemento.insert(1, firma)  # después del Issuer, como manda el esquema
    ref = xmlsec.template.add_reference(
        firma, xmlsec.Transform.SHA1 if sha1 else xmlsec.Transform.SHA256,
        uri=f"#{elemento.get('ID')}")
    xmlsec.template.add_transform(ref, xmlsec.Transform.ENVELOPED)
    xmlsec.template.add_transform(ref, xmlsec.Transform.EXCL_C14N)
    xmlsec.template.add_x509_data(xmlsec.template.ensure_key_info(firma))
    ctx = xmlsec.SignatureContext()
    ctx.key = xmlsec.Key.from_memory(par[0], xmlsec.KeyFormat.PEM)
    ctx.key.load_cert_from_memory(par[1], xmlsec.KeyFormat.PEM)
    ctx.register_id(elemento, "ID")
    ctx.sign(firma)


def _firmada(xml: str, par, *, asercion=True, respuesta=False, sha1=False):
    from lxml import etree

    doc = etree.fromstring(xml.encode())
    if asercion:
        _firmar(doc.find("saml:Assertion", NS), par, sha1)
    if respuesta:
        _firmar(doc, par, sha1)
    return doc


def _envuelta(doc, por_dentro: bool):
    """La aserción firmada se queda, para que la firma valga, y al lado o por fuera va
    otra que dice que quien entra es Ana, la propietaria."""
    buena = doc.find("saml:Assertion", NS)
    mala = copy.deepcopy(buena)
    mala.set("ID", "_mala")
    mala.find(".//saml:AttributeValue", NS).text = "ana@ejemplo.com"
    mala.find(".//saml:NameID", NS).text = "ana-idp"
    if por_dentro:
        # La firma de la buena, arriba en la mala, apuntando a la buena, que va dentro de
        # la firma, en un `ds:Object`.
        from lxml import etree

        firma = buena.find("ds:Signature", NS)
        buena.remove(firma)
        mala.remove(mala.find("ds:Signature", NS))
        mala.insert(1, firma)
        doc.replace(buena, mala)
        etree.SubElement(firma, f"{{{NS['ds']}}}Object").append(buena)
    else:
        mala.remove(mala.find("ds:Signature", NS))
        buena.addprevious(mala)
    return doc


def _xml(doc) -> str:
    from lxml import etree

    return etree.tostring(doc).decode() if not isinstance(doc, str) else doc


# ---------------------------------------------------------------------------------
# El viaje
# ---------------------------------------------------------------------------------


@pytest.fixture
def base(app, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app.state.settings, "alerts_base_url", BASE)


def _con_saml(app, certs):  # noqa: F811
    admin = _cliente(app)
    _configurar(admin)
    org = _org(admin)
    r = admin.put("/api/org/sso/domains", json={"org_id": org, "domains": ["ejemplo.com"]},
                  headers=H)
    assert r.status_code == 200, r.text
    r = admin.put("/api/org/sso", json={
        "org_id": org, "protocol": "saml", "issuer": IDP, "client_id": "",
        "sso_url": IDP_SSO, "idp_cert": certs["bueno"][1].decode(), "default_role": "lector",
    }, headers=H)
    assert r.status_code == 200, r.text
    estado = r.json()
    assert estado["protocol"] == "saml" and estado["saml_entity_id"] == SP
    assert estado["saml_acs_url"] == ACS
    return admin, org


def _empezar(app, cliente=None):  # noqa: F811
    c = cliente or _cliente(app)
    r = c.post("/api/auth/sso/start", json={"email": "luis@ejemplo.com", "next": "/trazas"},
               headers=H)
    assert r.status_code == 200, r.text
    url = urllib.parse.urlparse(r.json()["url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == IDP_SSO
    q = urllib.parse.parse_qs(url.query)
    peticion = zlib.decompress(base64.b64decode(q["SAMLRequest"][0]), -15).decode()
    request_id = re.search(r' ID="([^"]+)"', peticion).group(1)
    return c, q["RelayState"][0], request_id


def _volver(c, relay: str, doc):
    return c.post("/api/auth/sso/saml/acs", data={
        "SAMLResponse": base64.b64encode(_xml(doc).encode()).decode(), "RelayState": relay,
    }, follow_redirects=False)


def _fuera(r) -> str:
    assert r.status_code == 303, r.text
    destino = r.headers["location"]
    assert destino.startswith("/entrar?sso_error="), destino
    assert "laplace_session" not in r.cookies
    return destino.split("=", 1)[1]


@con_xmlsec
def test_entrar_por_saml_y_la_misma_respuesta_no_sirve_dos_veces(app, certs, base):  # noqa: F811
    _, org = _con_saml(app, certs)
    cuentas = app.state.cuentas
    c, relay, request_id = _empezar(app)
    doc = _firmada(_respuesta(request_id), certs["bueno"], respuesta=True)
    r = _volver(c, relay, doc)
    assert r.status_code == 303 and r.headers["location"] == "/trazas", r.headers
    assert "laplace_session" in r.cookies
    luis = cuentas.usuario_por_email("luis@ejemplo.com")[0]
    assert cuentas.rol_en(org, luis.id) == "lector" and cuentas.verificado(luis.id)
    assert c.get("/api/auth/me").json()["user"]["email"] == "luis@ejemplo.com"

    # Repetida en el mismo navegador: el `state` ya se gastó.
    assert _fuera(_volver(c, relay, doc)) == "estado"
    # Repetida con una petición nueva: no es la respuesta a esa petición.
    c2, relay2, _ = _empezar(app)
    assert _fuera(_volver(c2, relay2, doc)) == "token"
    # Con el `state` bueno y otro navegador, tampoco.
    _, relay3, request_id3 = _empezar(app)
    otro = _cliente(app)
    assert _fuera(_volver(otro, relay3, _firmada(_respuesta(request_id3), certs["bueno"]))) \
        == "estado"
    rechazos = [a for a in cuentas.auditoria(org) if a["action"] == "sso_rechazado"]
    assert rechazos, "los rechazos van a la auditoría"


ATAQUES = {
    "sin firma": lambda rid, c: _firmada(_respuesta(rid), c["bueno"], asercion=False),
    "firma de otro certificado": lambda rid, c: _firmada(_respuesta(rid), c["otro"]),
    "sólo la respuesta firmada": lambda rid, c: _firmada(
        _respuesta(rid), c["bueno"], asercion=False, respuesta=True),
    "respuesta firmada por otro": lambda rid, c: _firmar_otra(rid, c),
    "SHA-1": lambda rid, c: _firmada(_respuesta(rid), c["bueno"], sha1=True),
    "envuelta al lado": lambda rid, c: _envuelta(_firmada(_respuesta(rid), c["bueno"]), False),
    "envuelta por dentro": lambda rid, c: _envuelta(_firmada(_respuesta(rid), c["bueno"]), True),
    "otra audiencia": lambda rid, c: _firmada(
        _respuesta(rid, audiencia="https://otro.sitio/saml"), c["bueno"]),
    "sin audiencia": lambda rid, c: _firmada(_respuesta(rid, audiencia=None), c["bueno"]),
    "otro destino": lambda rid, c: _firmada(
        _respuesta(rid, destino="https://otro.sitio/acs"), c["bueno"]),
    "otro emisor": lambda rid, c: _firmada(
        _respuesta(rid, emisor="https://otro.idp/metadata"), c["bueno"]),
    "caducada": lambda rid, c: _firmada(_respuesta(rid, vence=-600), c["bueno"]),
    "a otra petición": lambda rid, c: _firmada(_respuesta("_otra"), c["bueno"]),
    "correo de otro dominio": lambda rid, c: _firmada(
        _respuesta(rid, email="luis@otro.com"), c["bueno"]),
}


def _firmar_otra(rid, c):
    """La aserción, bien; la respuesta, firmada por otro: las dos firmas tienen que valer."""
    doc = _firmada(_respuesta(rid), c["bueno"])
    _firmar(doc, c["otro"])
    return doc


@con_xmlsec
@pytest.mark.parametrize("ataque", list(ATAQUES))
def test_lo_que_no_cuadra_no_entra(app, certs, base, ataque):  # noqa: F811
    _, org = _con_saml(app, certs)
    cuentas = app.state.cuentas
    c, relay, request_id = _empezar(app)
    codigo = _fuera(_volver(c, relay, ATAQUES[ataque](request_id, certs)))
    assert codigo in ("token", "dominio"), codigo
    assert c.get("/api/auth/me").json().get("user") is None
    assert cuentas.usuario_por_email("luis@ejemplo.com") is None
    ana = cuentas.usuario_por_email("ana@ejemplo.com")[0]
    assert cuentas.rol_en(org, ana.id) == "propietario"


def test_una_asercion_se_gasta_una_sola_vez(app):  # noqa: F811
    cuentas = app.state.cuentas
    caduca = time.time() + 300
    assert cuentas.gastar_asercion("org_x", "_a1", caduca) is True
    assert cuentas.gastar_asercion("org_x", "_a1", caduca) is False
    assert cuentas.gastar_asercion("org_x", "_a2", caduca) is True


@con_xmlsec
def test_la_asercion_repetida_no_entra_aunque_lo_demas_cuadre(app, certs, base, monkeypatch):  # noqa: F811
    """El `ID` de la aserción, gastado: la segunda línea de defensa, sin la primera."""
    _, org = _con_saml(app, certs)
    c, relay, request_id = _empezar(app)
    doc = _firmada(_respuesta(request_id), certs["bueno"])
    assert _volver(c, relay, doc).headers["location"] == "/trazas"
    # Como si el `state` no se gastara: vuelve a dar el mismo.
    monkeypatch.setattr(type(app.state.cuentas), "gastar_estado",
                        lambda self, state, nav: {"org_id": org, "nonce": request_id,
                                                  "verifier": "", "next": "/trazas"})
    assert _fuera(_volver(_cliente(app), relay, doc)) == "token"


def test_sin_xmlsec_saml_esta_apagado(app, base, monkeypatch):  # noqa: F811
    admin = _cliente(app)
    _configurar(admin)
    org = _org(admin)
    cuentas = app.state.cuentas
    cuentas.poner_dominios(org, ["ejemplo.com"], "")
    monkeypatch.setattr(saml, "disponible", lambda: False)
    r = admin.put("/api/org/sso", json={
        "org_id": org, "protocol": "saml", "issuer": IDP, "client_id": "",
        "sso_url": IDP_SSO, "idp_cert": "x", "default_role": "lector"}, headers=H)
    assert r.status_code == 400 and "xmlsec" in r.json()["detail"], "dice qué falta"
    assert admin.get("/api/org/sso", params={"org_id": org}, headers=H).json()[
        "saml_available"] is False
    # Aunque estuviera configurado de antes, ni empieza ni se acepta una vuelta.
    cuentas.guardar_sso(org, IDP, "", None, "lector", False, protocolo="saml",
                        sso_url=IDP_SSO, cert="x")
    c = _cliente(app)
    r = c.post("/api/auth/sso/start", json={"email": "luis@ejemplo.com"}, headers=H)
    assert r.status_code == 503
    cuentas.guardar_estado("st", org, "_peticion", "", "nav", "/")
    c.cookies.set("laplace_sso", "nav")
    r = c.post("/api/auth/sso/saml/acs", data={"SAMLResponse": "PHg+", "RelayState": "st"},
               follow_redirects=False)
    assert _fuera(r) == "saml"
    assert cuentas.usuario_por_email("luis@ejemplo.com") is None


@con_xmlsec
def test_en_https_la_cookie_del_navegador_viaja_en_el_post_del_proveedor(
    app, certs, monkeypatch  # noqa: F811
):
    """La vuelta de SAML es un POST desde otro sitio: con `SameSite=Lax` el navegador no
    mandaría la cookie que ata el `state` a este navegador, y nadie podría entrar."""
    monkeypatch.setattr(app.state.settings, "alerts_base_url", "https://laplace.ejemplo.com")
    admin = _cliente(app)
    _configurar(admin)
    org = _org(admin)
    cuentas = app.state.cuentas
    cuentas.poner_dominios(org, ["ejemplo.com"], "")
    cuentas.guardar_sso(org, IDP, "", None, "lector", False, protocolo="saml",
                        sso_url=IDP_SSO, cert=certs["bueno"][1].decode())
    r = _cliente(app).post("/api/auth/sso/start", json={"email": "luis@ejemplo.com"}, headers=H)
    assert r.status_code == 200, r.text
    cookie = r.headers["set-cookie"].lower()
    assert "samesite=none" in cookie and "secure" in cookie, cookie
