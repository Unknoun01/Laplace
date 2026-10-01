"""Margen por cliente, más allá (D-179).

* Lo evitable de cada problema, repartido entre los clientes por lo que gastó cada uno
  en el paso: nunca suma más que el problema, y lo del trabajo sin cliente no se le da
  a nadie. Con el margen que quedaría arreglándolos.
* Ingresos en otra moneda, convertidos con el tipo que pone el usuario; sin tipo, no se
  convierte con uno inventado.
* Stripe guarda a cada cliente en su moneda y sólo convierte al que paga en varias.
* El cliente de cada traza, en la lista y en los dos almacenes.
"""

from __future__ import annotations

import importlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_margen import AHORA, VENTANA, _ejecucion, _repetida, _sembrar, almacen  # noqa: F401
from test_stripe import CLAVE, _factura, _linea

from laplace_backend import margen, stripe_ingresos
from laplace_backend.storage.base import TraceFilter
from laplace_backend.storage.metadata import SQLiteMetadataStore
from laplace_backend.storage.sqlite import SQLiteStore


def _problema(id_: str, paso: str, dinero: float, **extra):
    return SimpleNamespace(
        id=id_, kind="modelo_caro", step_key=paso, window_waste_usd=dinero,
        step_shares=extra.get("shares", {}), title=id_,
    )


def test_lo_evitable_se_reparte_por_lo_que_gasto_cada_cliente_en_el_paso():
    costes = {"acme": {"p": 3.0, "q": 1.0}, "beta": {"p": 1.0}, "": {"p": 4.0}}
    problemas = [_problema("a", "p", 8.0), _problema("b", "q", 2.0)]
    salida = margen.evitable_por_cliente(problemas, costes)
    assert salida["acme"] == pytest.approx({"a": 3.0, "b": 2.0})
    assert salida["beta"] == pytest.approx({"a": 1.0})
    assert "" not in salida, "lo del trabajo sin cliente no es de nadie"
    # Lo de los clientes nunca pasa del problema: aquí falta la mitad de «a», sin cliente.
    assert sum(salida["acme"].values()) + sum(salida["beta"].values()) == pytest.approx(6.0)


def test_un_problema_de_varios_pasos_se_reparte_por_sus_pasos():
    costes = {"acme": {"p": 1.0}, "beta": {"q": 1.0}}
    compartido = _problema("c", "p", 4.0, shares={"p": 3.0, "q": 1.0})
    salida = margen.evitable_por_cliente([compartido], costes)
    assert salida == {"acme": {"c": 3.0}, "beta": {"c": 1.0}}


def test_cada_cliente_sabe_cuanto_de_lo_evitable_es_suyo(almacen):  # noqa: F811
    from laplace_backend import insights

    store, proyecto = almacen
    spans = []
    for d in range(4):
        spans += _repetida(proyecto, "acme", AHORA - timedelta(days=d, hours=1))
        spans += _ejecucion(proyecto, "beta", 0.01, AHORA - timedelta(days=d, hours=2))
    store.insert_spans(spans)
    hallazgos = insights.detect(store, proyecto, VENTANA)
    repeticion = next(f for f in hallazgos if f.kind == "repeticion")
    vista = margen.calcular(store, proyecto, VENTANA, {"acme": 0.01})
    costes = store.customer_step_costs(proyecto, VENTANA)
    assert costes["acme"]["paso-buscar"] == pytest.approx(4 * 3 * 0.01)
    margen.con_problemas(
        vista, hallazgos, store.customer_steps(proyecto, VENTANA), costes, base=4.0
    )
    acme = next(c for c in vista.customers if c.customer_id == "acme")
    ref = next(f for f in acme.findings if f.id == repeticion.id)
    # El paso es sólo de acme: lo evitable de la repetición es todo suyo.
    assert ref.avoidable_usd == pytest.approx(repeticion.window_waste_usd)
    assert acme.avoidable_usd >= ref.avoidable_usd
    assert acme.avoidable_monthly_usd == pytest.approx(acme.avoidable_usd / 4.0 * 30)
    if acme.margin_usd is not None:
        assert acme.margin_after_fix_usd == pytest.approx(
            acme.margin_usd + acme.avoidable_monthly_usd
        )
    beta = next(c for c in vista.customers if c.customer_id == "beta")
    assert beta.avoidable_usd == pytest.approx(
        sum(v for v in margen.evitable_por_cliente(hallazgos, costes).get("beta", {}).values())
    )


def test_ingresos_en_otra_moneda_con_y_sin_tipo_de_cambio(almacen):  # noqa: F811
    store, proyecto = almacen
    _sembrar(store, proyecto)
    # acme cuesta unos 30 $ al mes; paga 25 € al mes.
    ingresos = {"acme": margen.Ingreso(25.0, "EUR")}
    sin_tipo = margen.calcular(store, proyecto, VENTANA, ingresos)
    acme = next(c for c in sin_tipo.customers if c.customer_id == "acme")
    assert acme.status == "sin-cambio" and acme.monthly_revenue is None
    assert acme.revenue_amount == 25.0 and acme.revenue_currency == "EUR"
    assert "EUR" in acme.headline
    con_tipo = margen.calcular(store, proyecto, VENTANA, ingresos, {"EUR": 1.10})
    acme = next(c for c in con_tipo.customers if c.customer_id == "acme")
    assert acme.monthly_revenue == pytest.approx(27.5)
    assert acme.status == "pierde"
    assert acme.margin_usd == pytest.approx(27.5 - acme.monthly_cost_usd)


def test_moneda_y_tipos_por_la_api(tmp_path, monkeypatch):
    db = tmp_path / "laplace.db"
    store = SQLiteStore(db)
    store.migrate()
    spans = []
    for d in range(3):
        spans += _ejecucion("local", "acme", 1.0, AHORA - timedelta(days=d, hours=1))
    store.insert_spans(spans)
    monkeypatch.setenv("LAPLACE_STORE", "sqlite")
    monkeypatch.setenv("LAPLACE_SQLITE_PATH", str(db))
    monkeypatch.setenv("LAPLACE_POSTGRES_ENABLED", "false")
    monkeypatch.setenv("LAPLACE_HOME", str(tmp_path))
    from laplace_backend import config, main

    config.get_settings.cache_clear()
    importlib.reload(main)
    with TestClient(main.app) as client:
        r = client.put(
            "/api/customers/revenue",
            json={"project_id": "local", "customer_id": "acme", "monthly": 100,
                  "currency": "eur"},
        )
        assert r.status_code == 200 and r.json()["currency"] == "EUR"
        vista = client.get("/api/customers", params={"project_id": "local", "days": 7}).json()
        assert vista["customers"][0]["status"] == "sin-cambio"
        malo = client.put("/api/exchange-rates", json={"project_id": "local",
                                                       "rates": {"EURO": 1.1}})
        assert malo.status_code == 422
        cero = client.put("/api/exchange-rates", json={"project_id": "local",
                                                       "rates": {"EUR": 0}})
        assert cero.status_code == 422
        ok = client.put("/api/exchange-rates", json={"project_id": "local",
                                                     "rates": {"eur": 1.1}})
        assert ok.json() == {"rates": {"EUR": 1.1}}
        assert client.get("/api/exchange-rates", params={"project_id": "local"}).json() == {
            "rates": {"EUR": 1.1}
        }
        vista = client.get("/api/customers", params={"project_id": "local", "days": 7}).json()
        (acme,) = vista["customers"]
        assert acme["monthly_revenue"] == pytest.approx(110.0)
        assert acme["revenue_currency"] == "EUR" and acme["revenue_amount"] == 100
    config.get_settings.cache_clear()


def _pedir(facturas):
    return lambda url, clave: {"data": facturas, "has_more": False}


def test_stripe_guarda_cada_cliente_en_su_moneda():
    facturas = [
        _factura("in_1", "acme", [_linea(10)], moneda="eur"),
        _factura("in_2", "beta", [_linea(50)]),
        _factura("in_3", "gama", [_linea(20)], moneda="eur"),
        _factura("in_4", "gama", [_linea(10)]),
        _factura("in_5", "delta", [_linea(30)], moneda="gbp"),
        _factura("in_6", "delta", [_linea(10)]),
    ]
    r = stripe_ingresos.sincronizar(CLAVE, AHORA, pedir=_pedir(facturas), tipos={"EUR": 1.1})
    assert r.revenue["acme"] == 10.0 and r.currencies["acme"] == "EUR"
    assert r.revenue["beta"] == 50.0 and "beta" not in r.currencies
    # Varias monedas: a dólares con el tipo puesto.
    assert r.revenue["gama"] == pytest.approx(20 * 1.1 + 10) and "gama" not in r.currencies
    # La libra no tiene tipo: esa factura no se cuenta, y se dice.
    assert r.revenue["delta"] == 10.0 and r.other_currency == 1


def test_stripe_aplica_la_moneda(tmp_path):
    meta = SQLiteMetadataStore(tmp_path / "m.db")
    meta.migrate()
    r = stripe_ingresos.sincronizar(
        CLAVE, AHORA, pedir=_pedir([_factura("in_1", "acme", [_linea(10)], moneda="eur")])
    )
    stripe_ingresos.aplicar(meta, "p", r)
    assert margen.leer_ingresos(meta, "p")["acme"] == margen.Ingreso(10.0, "EUR")


def test_el_cliente_de_cada_traza_en_la_lista(almacen):  # noqa: F811
    store, proyecto = almacen
    _sembrar(store, proyecto, dias=1)
    pagina = store.list_traces(
        TraceFilter(project_id=proyecto, since=VENTANA.since, until=VENTANA.until)
    )
    clientes = sorted((t.customer_id or "") for t in pagina.traces)
    assert clientes == ["", "acme", "beta"]
