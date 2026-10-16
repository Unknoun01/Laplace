"""La huella de lo parecido (D-219): qué separa y qué no, con las medidas de D-219."""

from __future__ import annotations

from laplace_backend.ingest.otlp import simhash

BASE = (
    "Eres el asistente de una tienda online. El cliente escribe: Hola, hice un pedido la "
    "semana pasada con tres camisetas de algodón y un pantalón vaquero, pagué con tarjeta "
    "y recibí el correo de confirmación, pero hasta hoy el paquete no ha llegado a mi casa "
    "y el seguimiento de la empresa de transporte lleva cuatro días sin cambiar de estado. "
    "Vivo en un tercer piso sin ascensor y siempre hay alguien en casa por las tardes. "
    "Quiero saber si el paquete se ha perdido, si debo esperar más o si puedo pedir que me "
    "devuelvan el dinero. Gracias de antemano por vuestra ayuda con este asunto"
)


def _msgs(texto, sistema="Responde con el manual."):
    return [{"role": "system", "content": sistema}, {"role": "user", "content": texto}]


def _d(a, b):
    return bin((simhash(_msgs(a)) ^ simhash(_msgs(b))) & (2**64 - 1)).count("1")


def test_los_numeros_y_las_mayusculas_no_mueven_la_huella():
    assert _d(BASE + " 4471", BASE + " 9902") == 0
    assert _d(BASE, BASE.upper()) == 0


def test_una_palabra_en_un_texto_largo_esta_dentro_del_umbral():
    assert 0 < _d(BASE, BASE.replace("camisetas", "blusas")) <= 4


def test_un_texto_distinto_esta_fuera():
    otro = (
        BASE.replace("tres camisetas de algodón y un pantalón vaquero", "dos libros")
        .replace("tercer piso sin ascensor", "bajo con jardín")
        .replace("cuatro días", "dos semanas")
    )
    assert _d(BASE, otro) > 4


def test_el_sentido_contrario_no_se_distingue_por_la_huella_sola():
    """Lo que enseñaron las medidas: la negación, en un texto largo, queda a 9 bits;
    en uno corto, más cerca que un sinónimo. Por eso la huella no decide nada."""
    assert _d(BASE, BASE.replace("no ha llegado", "ya ha llegado")) > 4


def test_las_instrucciones_de_sistema_no_cuentan():
    a = simhash(_msgs(BASE, sistema="Eres amable y breve."))
    b = simhash(_msgs(BASE, sistema="Responde siempre en verso y con mucho detalle."))
    assert a == b != 0


def test_con_menos_de_cien_palabras_no_hay_huella():
    assert simhash(_msgs("Dime el estado de mi pedido número 4471 por favor")) == 0
    assert simhash([]) == 0
    assert simhash(None) == 0


def test_cabe_en_un_entero_con_signo_de_64_bits():
    assert -(2**63) <= simhash(_msgs(BASE)) < 2**63
