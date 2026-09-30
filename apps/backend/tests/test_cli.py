"""La línea de órdenes `laplace`."""

from __future__ import annotations

import io
import sys

from laplace import cli


def test_la_salida_va_en_utf8_aunque_la_consola_no(monkeypatch):
    """La consola de Windows escribe en cp1252: las tildes y las «comillas» de los textos
    salían rotas. `main` pide UTF-8 a la salida antes de escribir nada."""
    crudo = io.BytesIO()
    consola = io.TextIOWrapper(crudo, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", consola)
    cli.main([])  # sin orden: imprime la ayuda
    consola.flush()
    assert "Observabilidad y optimización".encode() in crudo.getvalue()
