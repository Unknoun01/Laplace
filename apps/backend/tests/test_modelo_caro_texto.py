"""La ficha del modelo caro tiene que explicar ESTE paso, no un paso cualquiera.

Se vio en pantalla con la demo: la ficha decía «los modelos grandes se pagan sobre todo
por lo que escriben» de un paso que recibe 20.000 tokens de manual y contesta 19. Casi
todo su coste era entrada, y ese paso —contestar con documentos delante— es justo donde
un modelo pequeño tiene más riesgo de equivocarse. Un texto genérico que contradice la
cifra de al lado es la misma clase de fallo que un «no lo sabemos» falso (D-114).
"""

from __future__ import annotations

from laplace_backend.insights import modelo_caro
from laplace_backend.storage.base import ModelUsage, WindowSummary


def _uso(entrada_por_llamada: int, salida_por_llamada: int, llamadas: int = 32) -> ModelUsage:
    return ModelUsage(
        key="k-responder",
        name="responder",
        model="gpt-5.6-terra",
        calls=llamadas,
        traces=16,
        input_tokens=entrada_por_llamada * llamadas,
        output_tokens=salida_por_llamada * llamadas,
        avg_input_tokens=float(entrada_por_llamada),
        avg_output_tokens=float(salida_por_llamada),
        min_input_tokens=entrada_por_llamada,
        p50_output_tokens=float(salida_por_llamada),
    )


def _ficha(uso: ModelUsage):
    resumen = WindowSummary(traces=16, llm_calls=uso.calls)
    hallazgo = modelo_caro._expensive_model_finding(uso, resumen, 2.7, 2.7)
    assert hallazgo is not None, "el escenario tiene que disparar la regla"
    return hallazgo, modelo_caro._expensive_model_detail(hallazgo, uso, "SELECT 1")


def test_con_mucho_contexto_la_ficha_habla_de_la_entrada_y_avisa_del_riesgo():
    hallazgo, ficha = _ficha(_uso(20_000, 19))
    assert "escriben" not in ficha.why, (
        "casi todo el coste es entrada: decir que se paga por lo que se escribe es falso"
    )
    assert "entrada" in ficha.why, ficha.why
    assert "contexto" in ficha.why, "con 20.000 tokens delante hay que avisar del riesgo"
    assert "paso muy corto" not in hallazgo.title, hallazgo.title


def test_con_poca_entrada_la_ficha_habla_de_la_salida():
    _, ficha = _ficha(_uso(300, 5))
    assert "contexto" not in ficha.why, ficha.why


def test_la_ficha_no_dice_que_la_evaluacion_no_existe():
    """Evaluaciones existe desde la Fase 5 y la misma ficha ofrece crear el conjunto."""
    _, ficha = _ficha(_uso(300, 5))
    for paso in ficha.fix_steps:
        assert "Cuando exista" not in paso.body, paso.body
        assert "toca a mano" not in paso.body, paso.body
