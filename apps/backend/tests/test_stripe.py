"""Ingresos por cliente desde Stripe (D-162), contra una Stripe falsa.

Lo que se exige: que cada factura se lleve al cliente de las trazas correcto
(`metadata.laplace_customer_id` antes que el id), que un plan anual cuente como su parte
de un mes, que lo que no está en dólares no se convierta, que se pagine entero, que lo
puesto a mano para un cliente que Stripe no conoce se quede, y que la clave no se
devuelva nunca entera.
"""

from __future__ import annotations

import importlib
import urllib.parse
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from laplace_backend import margen, stripe_ingresos
from laplace_backend.storage.metadata import SQLiteMetadataStore

AHORA = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
DIA = 86400
CLAVE = "rk_test_" + "x" * 24


def _factura(id_, cliente, lineas, moneda="usd"):
    return {"id": id_, "currency": moneda, "customer": cliente, "lines": {"data": lineas}}


def _linea(dolares, dias=None):
    linea = {"amount": int(dolares * 100)}
    if dias:
        linea["period"] = {"start": 1_700_000_000, "end": 1_700_000_000 + dias * DIA}
    return linea


class StripeFalsa:
    """Dos páginas de facturas, como las devuelve la API con `expand[]=data.customer`."""

    def __init__(self, paginas):
        self.paginas = paginas
        self.pedidas: list[dict[str, list[str]]] = []

    def __call__(self, url: str, clave: str):
        assert clave == CLAVE
        consulta = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.pedidas.append(consulta)
        return self.paginas[len(self.pedidas) - 1]


def _stripe():
    acme = {"id": "cus_A", "metadata": {"laplace_customer_id": "acme"}}
    beta = {"id": "cus_B", "metadata": {}}
    return StripeFalsa([
        {
            "has_more": True,
            "data": [
                _factura("in_1", acme, [_linea(90, dias=30)]),
                # Anual: 1.200 $ son 100 al mes, no 1.200.
                _factura("in_2", beta, [_linea(1200, dias=365)]),
            ],
        },
        {
            "has_more": False,
            "data": [
                # Un cargo suelto, sin periodo: cuenta entero.
                _factura("in_3", acme, [_linea(10)]),
                _factura("in_4", beta, [_linea(500, dias=30)], moneda="eur"),
            ],
        },
    ])


def test_las_facturas_se_llevan_al_cliente_y_al_mes():
    falsa = _stripe()
    r = stripe_ingresos.sincronizar(CLAVE, AHORA, pedir=falsa)
    assert r.revenue == {"acme": 100.0, "cus_B": pytest.approx(98.63, abs=0.01)}
    assert r.invoices == 4 and r.other_currency == 1
    # Sólo pagadas, del último mes, con el cliente expandido; y la segunda página sigue
    # a la última factura de la primera.
    primera, segunda = falsa.pedidas
    assert primera["status"] == ["paid"] and primera["expand[]"] == ["data.customer"]
    assert int(primera["created[gte]"][0]) == int(AHORA.timestamp()) - 30 * DIA
    assert segunda["starting_after"] == ["in_2"]


def test_la_clave_se_reconoce_por_su_forma():
    assert stripe_ingresos.clave_valida(CLAVE)
    assert stripe_ingresos.clave_valida("sk_live_" + "y" * 30)
    assert not stripe_ingresos.clave_valida("pk_live_" + "y" * 30), "la publicable no lee nada"
    assert not stripe_ingresos.clave_valida("sk_test_corta")


def test_aplicar_respeta_lo_puesto_a_mano_para_quien_stripe_no_conoce(tmp_path):
    meta = SQLiteMetadataStore(tmp_path / "m.db")
    meta.migrate()
    meta.set_setting("p", margen.clave("gamma"), {"monthly": 40.0})  # a mano, no está en Stripe
    meta.set_setting("p", margen.clave("acme"), {"monthly": 1.0})  # a mano, Stripe lo pisa
    meta.set_setting(
        "p", margen.clave("viejo"), {"monthly": 70.0, "source": "stripe"}
    )  # de Stripe, ya no paga
    r = stripe_ingresos.sincronizar(CLAVE, AHORA, pedir=_stripe())
    stripe_ingresos.aplicar(meta, "p", r)
    ingresos = margen.leer_ingresos(meta, "p")
    assert ingresos["gamma"] == 40.0
    assert ingresos["acme"] == 100.0
    assert "viejo" not in ingresos
    fuentes = margen.leer_fuentes(meta, "p")
    assert fuentes["acme"] == "stripe" and fuentes["gamma"] == "manual"
    assert stripe_ingresos.estado(meta, "p").last_sync == AHORA


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    from laplace_backend.storage.sqlite import SQLiteStore

    db = tmp_path / "laplace.db"
    SQLiteStore(db).migrate()
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(db))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    monkeypatch.setenv("LAPLACE_HOME", str(tmp_path))
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c
    config.get_settings.cache_clear()


def test_la_api_guarda_la_clave_sin_devolverla_y_sincroniza(cliente, monkeypatch):
    mala = cliente.put("/api/stripe", json={"project_id": "p", "api_key": "pk_x"})
    assert mala.status_code == 422
    sin = cliente.post("/api/stripe/sync", json={"project_id": "p"})
    assert sin.status_code == 409

    puesta = cliente.put("/api/stripe", json={"project_id": "p", "api_key": CLAVE})
    assert puesta.status_code == 200
    assert puesta.json()["key_hint"] == "…xxxx"
    assert CLAVE not in puesta.text
    assert CLAVE not in cliente.get("/api/stripe", params={"project_id": "p"}).text

    monkeypatch.setattr(stripe_ingresos, "_pedir", _stripe())
    hecho = cliente.post("/api/stripe/sync", json={"project_id": "p"})
    assert hecho.status_code == 200, hecho.text
    assert hecho.json()["customers"] == 2
    assert "otra moneda" in hecho.json()["detail"]


def test_si_stripe_rechaza_la_clave_se_dice(cliente, monkeypatch):
    cliente.put("/api/stripe", json={"project_id": "p", "api_key": CLAVE})

    def rechaza(url, clave):
        raise stripe_ingresos.StripeError("stripe.clave_rechazada")

    monkeypatch.setattr(stripe_ingresos, "_pedir", rechaza)
    respuesta = cliente.post("/api/stripe/sync", json={"project_id": "p"})
    assert respuesta.status_code == 502
    assert "no acepta esa clave" in respuesta.json()["detail"]
