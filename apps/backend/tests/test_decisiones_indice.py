"""El índice de `DECISIONS.md` se genera y tiene que estar al día (D-174)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]


def test_el_indice_de_decisiones_esta_al_dia():
    resultado = subprocess.run(
        [sys.executable, str(RAIZ / "scripts" / "indice_decisiones.py"), "--comprobar"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr


def test_toda_decision_sale_en_el_indice():
    indice = (RAIZ / "docs" / "decisiones-indice.md").read_text(encoding="utf-8")
    decisiones = (RAIZ / "DECISIONS.md").read_text(encoding="utf-8")
    import re

    ids = re.findall(r"^### (D-\d+[a-z]?) — ", decisiones, flags=re.M)
    assert ids and all(f"[{d}]" in indice for d in ids)
