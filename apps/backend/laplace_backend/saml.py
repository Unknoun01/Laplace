"""Entrar con el proveedor de identidad de la organización, por SAML 2.0 (D-211).

Verificar una firma XML a mano es donde se rompen los inicios de sesión (*signature
wrapping*), así que no se hace a mano: `python3-saml`, encima de `xmlsec`, que ya exige
una sola aserción, que la referencia de la firma sea el `ID` del elemento firmado y que
los datos salgan de lo que se validó. Es un extra (`pip install laplace-backend[saml]`):
sin él, SAML está apagado y se dice. Nunca se lee una respuesta sin validar su firma.

Además de lo que comprueba la biblioteca en modo estricto (emisor, destino, `Recipient`,
caducidad, `InResponseTo`):
* la aserción va firmada siempre, con el certificado de la organización; si la respuesta
  también va firmada, esa firma también tiene que valer;
* sin SHA-1 ni aserciones cifradas;
* la audiencia tiene que estar, y ser Laplace: sin ella, la biblioteca la da por buena;
* el `ID` de la aserción se gasta (en `cuentas.py`), aparte del `state` de un solo uso.
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any

from .sso import Persona, SSOError, _https, dominio_de

logger = logging.getLogger("laplace.saml")

try:  # sin el extra, o con `lxml` y `xmlsec` de `libxml2` distintas, falla al importar
    import xmlsec
    from onelogin.saml2.authn_request import OneLogin_Saml2_Authn_Request
    from onelogin.saml2.constants import OneLogin_Saml2_Constants
    from onelogin.saml2.response import OneLogin_Saml2_Response
    from onelogin.saml2.settings import OneLogin_Saml2_Settings
    from onelogin.saml2.utils import OneLogin_Saml2_Utils

    _FALTA = ""
except Exception as exc:  # noqa: BLE001 - cualquier fallo al importar apaga SAML
    _FALTA = f"{type(exc).__name__}: {exc}"

#: Dónde mira el correo cada proveedor: el nombre corto (Okta, Google), el de Microsoft
#: (Entra ID, ADFS) y el OID de LDAP.
ATRIBUTOS_CORREO = (
    "email", "mail", "Email", "emailAddress",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "urn:oid:0.9.2342.19200300.100.1.3",
)
ATRIBUTOS_NOMBRE = (
    "displayName", "name", "http://schemas.microsoft.com/identity/claims/displayname",
    "urn:oid:2.16.840.1.113730.3.1.241",
)


def disponible() -> bool:
    return not _FALTA


def entity_id(base: str) -> str:
    return f"{base}/api/auth/sso/saml"


def acs(base: str) -> str:
    return f"{base}/api/auth/sso/saml/acs"


def comprobar_config(issuer: str, sso_url: str, cert: str) -> None:
    """`ValueError` con el motivo si la configuración no sirve."""
    if not disponible():
        raise ValueError(f"falta el extra saml: {_FALTA}")
    if not issuer.strip():
        raise ValueError("falta el entityID del proveedor")
    try:
        _https(sso_url)
    except SSOError as exc:
        raise ValueError(str(exc)) from exc
    try:
        xmlsec.Key.from_memory(OneLogin_Saml2_Utils.format_cert(cert), xmlsec.KeyFormat.CERT_PEM)
    except Exception as exc:  # noqa: BLE001 - xmlsec.Error, o lo que no es un PEM
        raise ValueError(f"el certificado no se puede leer: {exc}") from exc


def _ajustes(config: Any, base: str) -> Any:
    return OneLogin_Saml2_Settings(
        {
            "strict": True,
            "sp": {
                "entityId": entity_id(base),
                "assertionConsumerService": {
                    "url": acs(base), "binding": OneLogin_Saml2_Constants.BINDING_HTTP_POST,
                },
                "NameIDFormat": OneLogin_Saml2_Constants.NAMEID_UNSPECIFIED,
            },
            "idp": {
                "entityId": config.issuer,
                "singleSignOnService": {
                    "url": config.sso_url,
                    "binding": OneLogin_Saml2_Constants.BINDING_HTTP_REDIRECT,
                },
                "x509cert": config.idp_cert,
            },
            "security": {
                "wantAssertionsSigned": True,
                "wantMessagesSigned": False,  # Okta y Entra ID firman sólo la aserción
                "wantAssertionsEncrypted": False,
                "wantNameId": True,
                "authnRequestsSigned": False,
                "requestedAuthnContext": False,
                "rejectDeprecatedAlgorithm": True,
            },
        },
        sp_validation_only=True,
    )


def empezar(config: Any, base: str, state: str) -> tuple[str, str]:
    """La URL del proveedor y el `ID` de la petición, que tiene que volver en la respuesta."""
    if not disponible():
        raise SSOError("saml", _FALTA)
    peticion = OneLogin_Saml2_Authn_Request(_ajustes(config, base))
    url = OneLogin_Saml2_Utils.redirect(
        config.sso_url, {"SAMLRequest": peticion.get_request(), "RelayState": state}
    )
    return url, peticion.get_id()


def _primero(atributos: dict[str, list[str]], nombres: tuple[str, ...]) -> str:
    return next((str(atributos[n][0]) for n in nombres if atributos.get(n)), "")


def terminar(
    config: Any, base: str, saml_response: str, request_id: str
) -> tuple[Persona, str, float]:
    """Quién es, el `ID` de la aserción y hasta cuándo vale (para gastarla). `SSOError`
    con `token` si la respuesta no cuadra, y con `dominio` si el correo no es suyo."""
    if not disponible():
        raise SSOError("saml", _FALTA)
    destino = urllib.parse.urlparse(acs(base))
    datos = {
        "https": "on" if destino.scheme == "https" else "off",
        "http_host": destino.netloc,
        "script_name": destino.path,
        "get_data": {},
        "post_data": {"SAMLResponse": saml_response},
    }
    try:
        respuesta = OneLogin_Saml2_Response(_ajustes(config, base), saml_response)
        respuesta.is_valid(datos, request_id=request_id, raise_exceptions=True)
        audiencias = respuesta.get_audiences()
        asercion = respuesta.get_assertion_id()
        caduca = respuesta.get_assertion_not_on_or_after()
        nameid = respuesta.get_nameid()
        atributos = respuesta.get_attributes()
    except Exception as exc:  # noqa: BLE001 - la biblioteca lanza de todo; todo es «no»
        raise SSOError("token", f"respuesta SAML: {type(exc).__name__}: {exc}") from exc
    if entity_id(base) not in audiencias:
        raise SSOError("token", f"audiencia {audiencias!r}")
    if not asercion or not nameid:
        raise SSOError("token", "sin ID de aserción o sin NameID")
    email = (_primero(atributos, ATRIBUTOS_CORREO) or (nameid if "@" in nameid else ""))
    email = email.strip().lower()
    if "@" not in email:
        raise SSOError("correo", "el proveedor no manda correo")
    if dominio_de(email) not in config.domains:
        raise SSOError("dominio", f"{dominio_de(email)} no es de la organización")
    persona = Persona(
        subject=f"saml:{nameid}", email=email, name=_primero(atributos, ATRIBUTOS_NOMBRE)
    )
    return persona, asercion, float(caduca or time.time() + 3600)
