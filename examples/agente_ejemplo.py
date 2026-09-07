"""Agente de ejemplo instrumentado con Laplace.

Sirve para dos cosas: probar la ingesta y la vista de árbol de punta a punta, y tener
datos realistas con los que diseñar el producto. No necesita claves de API: el modelo
es falso, pero los nombres de modelo y los recuentos de tokens son reales, así que el
coste que calcula el backend es el que costaría de verdad.

    python examples/agente_ejemplo.py

Variables:
    LAPLACE_ENDPOINT   por defecto http://localhost:8000
    LAPLACE_PROJECT    por defecto demo-viajes

El agente genera a propósito cinco patologías que el producto tiene que saber enseñar:

  1. La misma tool llamada tres veces con los mismos argumentos (un bucle).
  2. Un paso de clasificación trivial resuelto con un modelo caro.
  3. Un agente que itera y se atasca: traza larga (~30 pasos) con el bucle dentro.
  4. La misma llamada al modelo reintentada tres veces, que sí se paga.
  5. Un manual de 20.000 tokens reenviado entero en cada llamada, sin caché.
  6. El mismo agente ya arreglado, cacheando el manual: sirve para comparar y para
     comprobar que el motor de coste sabe reflejar la mejora que recomienda.
  7. Una traza que falla, con la excepción registrada en el span.
"""

from __future__ import annotations

import os
import random
import sys
import time

import laplace

# Modelos vigentes: comparar contra una generación retirada haría que las cifras se
# leyeran como un ejercicio de museo. `terra` cuesta diez veces lo que `luna`, que es
# justo la diferencia que la regla del modelo caro tiene que saber señalar.
MODELO_CARO = "gpt-5.6-terra"
MODELO_BARATO = "gpt-5.6-luna"

#: Instrucciones largas que van pegadas a cada petición del paso «consultar_manual»:
#: es el contexto fijo que la tercera regla detecta. 20.000 tokens de tarifas, reglas
#: de equipaje y política de cambios, reenviados enteros en cada llamada.
TOKENS_MANUAL = 20_000


# ---------------------------------------------------------------------------------
# Un "modelo" falso: latencia, tokens y respuesta plausibles.
# ---------------------------------------------------------------------------------


def llamar_modelo(modelo: str, mensajes: list[dict], respuesta: str, **params) -> str:
    """Simula una llamada a un LLM y la registra como span `llm`.

    `laplace.llm_span` emite exactamente los mismos atributos que las integraciones
    automáticas de OpenAI y Anthropic, así que el coste se calcula igual.
    """
    with laplace.llm_span(
        model=modelo, system="openai", input_messages=mensajes, **params
    ) as llm:
        entrada = sum(len(str(m.get("content", ""))) for m in mensajes) // 4 + 12
        salida = len(respuesta) // 4 + 8
        time.sleep(random.uniform(0.15, 0.6))
        llm.record_response(
            output_messages=[{"role": "assistant", "content": respuesta}],
            input_tokens=entrada,
            output_tokens=salida,
            response_model=modelo,
            finish_reasons=["stop"],
        )
    return respuesta


# ---------------------------------------------------------------------------------
# Herramientas
# ---------------------------------------------------------------------------------


@laplace.observe(type="tool")
def buscar_vuelos(origen: str, destino: str, fecha: str) -> list[dict]:
    """Herramienta falsa. Siempre devuelve lo mismo para los mismos argumentos."""
    time.sleep(random.uniform(0.05, 0.2))
    return [
        {"vuelo": "IB3421", "salida": f"{fecha}T08:15", "precio_eur": 148},
        {"vuelo": "VY1802", "salida": f"{fecha}T13:40", "precio_eur": 96},
    ]


@laplace.observe(type="tool")
def reservar(vuelo: str, pasajero: str) -> dict:
    """Falla siempre: sirve para ver cómo se pinta una traza con error."""
    time.sleep(0.08)
    raise RuntimeError(f"el proveedor rechazó la reserva del vuelo {vuelo}")


@laplace.observe(type="retrieval")
def recuperar_preferencias(usuario: str) -> list[str]:
    time.sleep(0.05)
    return ["prefiere vuelos por la mañana", "equipaje de mano únicamente"]


# ---------------------------------------------------------------------------------
# El agente
# ---------------------------------------------------------------------------------


@laplace.observe(type="chain")
def clasificar_intencion(pregunta: str) -> str:
    """Clasificar es una tarea trivial. Aquí se usa el modelo caro a propósito.

    Es exactamente el derroche que el panel de ahorro tendrá que señalar: el mismo
    resultado con el modelo pequeño de la misma familia costaría diez veces menos.
    """
    return llamar_modelo(
        MODELO_CARO,
        [
            {"role": "system", "content": "Clasifica la intención en una palabra."},
            {"role": "user", "content": pregunta},
        ],
        "busqueda_vuelos",
        temperature=0,
    )


@laplace.observe(type="chain")
def planificar(pregunta: str, preferencias: list[str]) -> dict:
    plan = llamar_modelo(
        MODELO_BARATO,
        [
            {"role": "system", "content": "Devuelve un plan en JSON."},
            {"role": "user", "content": f"{pregunta}\nPreferencias: {preferencias}"},
        ],
        '{"accion": "buscar_vuelos", "origen": "MAD", "destino": "BCN"}',
        temperature=0.2,
    )
    laplace.update_current_span(output=plan)
    return {"origen": "MAD", "destino": "BCN", "fecha": "2026-10-14"}


@laplace.observe(type="chain")
def redactar(pregunta: str, vuelos: list[dict], preferencias: list[str]) -> str:
    return llamar_modelo(
        MODELO_CARO,
        [
            {"role": "system", "content": "Responde en español, breve y concreto."},
            {
                "role": "user",
                "content": f"Pregunta: {pregunta}\nVuelos: {vuelos}\nPreferencias: {preferencias}",
            },
        ],
        "Te recomiendo el IB3421, sale a las 08:15 y cuesta 148 €. "
        "Encaja con tu preferencia por vuelos de mañana.",
        temperature=0.7,
        max_tokens=300,
    )


@laplace.observe(type="agent", name="agente_de_viajes")
def responder(pregunta: str, usuario: str = "u-7") -> str:
    """Una ejecución completa del agente ante una petición."""
    preferencias = recuperar_preferencias(usuario)
    clasificar_intencion(pregunta)
    plan = planificar(pregunta, preferencias)

    # Bucle: el agente reintenta la misma búsqueda tres veces sin cambiar nada.
    # Mismos argumentos => mismo dedup_hash => el backend puede detectarlo.
    vuelos: list[dict] = []
    for _ in range(3):
        vuelos = buscar_vuelos(plan["origen"], plan["destino"], plan["fecha"])

    return redactar(pregunta, vuelos, preferencias)


@laplace.observe(type="tool")
def consultar_precio(vuelo: str) -> dict:
    time.sleep(random.uniform(0.03, 0.12))
    return {"vuelo": vuelo, "precio_eur": 148 if vuelo == "IB3421" else 96}


@laplace.observe(type="chain")
def iteracion(numero: int, pregunta: str, historial: list[str]) -> str:
    """Un ciclo razonar → actuar. Es el patrón que llena de pasos una traza real."""
    decision = llamar_modelo(
        MODELO_BARATO,
        [
            {"role": "system", "content": "Decide la siguiente acción."},
            {"role": "user", "content": f"{pregunta}\nHasta ahora: {historial}"},
        ],
        f"accion_{numero}",
        temperature=0.1,
    )
    # A partir de la tercera vuelta el agente se atasca y repite la misma consulta:
    # mismos argumentos, mismo dedup_hash, bucle visible en el árbol.
    vuelo = "IB3421" if numero >= 3 else f"VY{1800 + numero}"
    consultar_precio(vuelo)
    buscar_vuelos("MAD", "BCN", "2026-10-14")
    return decision


@laplace.observe(type="agent", name="agente_iterativo")
def responder_iterando(pregunta: str, vueltas: int = 6) -> str:
    """Traza larga: ~40 pasos con un bucle a partir de la tercera vuelta.

    Sirve para comprobar que la vista de árbol sigue siendo legible y rápida cuando el
    agente no resuelve a la primera, que es cuando de verdad se necesita mirarla.
    """
    preferencias = recuperar_preferencias("u-7")
    historial: list[str] = []
    for numero in range(1, vueltas + 1):
        historial.append(iteracion(numero, pregunta, historial))
    return redactar(pregunta, buscar_vuelos("MAD", "BCN", "2026-10-14"), preferencias)


@laplace.observe(type="chain")
def extraer_datos(pregunta: str) -> dict:
    """Reintenta la MISMA llamada al modelo cuando no sabe leer la respuesta.

    Es un patrón habitual de verdad: el modelo devuelve algo que no parsea y el código
    reintenta sin cambiar el prompt. A diferencia de repetir una herramienta, esto sí
    se paga: cada reintento es una llamada al modelo completa.
    """
    mensajes = [
        {"role": "system", "content": "Devuelve origen y destino en JSON. Sólo JSON."},
        {"role": "user", "content": pregunta},
    ]
    for intento in range(3):
        # La respuesta viene cortada y no parsea, así que se reintenta igual que estaba.
        llamar_modelo(MODELO_CARO, mensajes, '{"origen": "MAD"', temperature=0)
        if intento == 2:  # al tercero se da por bueno y se sigue
            break
    return {"origen": "MAD", "destino": "BCN"}


@laplace.observe(type="chain")
def consultar_manual(pregunta: str, vuelta: int) -> str:
    """Reenvía el manual entero en cada llamada, sin marcar nada como cacheable.

    Es el derroche más silencioso de todos: no falla nada, no se repite ninguna llamada,
    simplemente se paga a tarifa completa un texto que el proveedor ya ha visto doce
    veces. Aquí se emite a propósito sin tokens de caché para que la tercera regla lo
    encuentre; en cuanto el agente marque ese bloque como cacheable, la regla deja de
    dispararse y el ahorro pasa a verse medido en el propio coste.
    """
    with laplace.llm_span(
        model=MODELO_CARO,
        system="openai",
        input_messages=[
            {"role": "system", "content": f"<manual de {TOKENS_MANUAL} tokens>"},
            {"role": "user", "content": f"{pregunta} (consulta {vuelta})"},
        ],
        temperature=0,
    ) as llm:
        time.sleep(random.uniform(0.05, 0.2))
        llm.record_response(
            output_messages=[{"role": "assistant", "content": "Equipaje de mano incluido."}],
            # Sin `cached_input_tokens`: nadie ha activado la caché de prompt.
            input_tokens=TOKENS_MANUAL + 40 + vuelta,
            output_tokens=18,
            response_model=MODELO_CARO,
            finish_reasons=["stop"],
        )
    return "Equipaje de mano incluido."


@laplace.observe(type="agent", name="agente_de_equipaje")
def responder_sobre_equipaje(pregunta: str, consultas: int = 6) -> str:
    """Seis consultas al manual dentro de una misma ejecución."""
    for vuelta in range(consultas):
        consultar_manual(pregunta, vuelta)
    return "Equipaje de mano incluido en todas las tarifas menos la básica."


@laplace.observe(type="chain")
def consultar_manual_cacheado(pregunta: str, vuelta: int) -> str:
    """El mismo paso, con el manual marcado como cacheable. El arreglo, ya aplicado.

    La primera consulta de cada ejecución escribe la caché (se paga a 1,25x) y las demás
    la leen (a un 10 %). Sirve para ver las dos caras: el hallazgo desaparece del panel y
    el dinero que la caché ahorra pasa a verse medido, no prometido.
    """
    primera = vuelta == 0
    with laplace.llm_span(
        model=MODELO_BARATO,
        system="openai",
        input_messages=[
            {"role": "system", "content": f"<manual de {TOKENS_MANUAL} tokens, cacheado>"},
            {"role": "user", "content": f"{pregunta} (consulta {vuelta})"},
        ],
        temperature=0,
    ) as llm:
        time.sleep(random.uniform(0.02, 0.08))
        llm.record_response(
            output_messages=[{"role": "assistant", "content": "Equipaje de mano incluido."}],
            input_tokens=TOKENS_MANUAL + 40 + vuelta,
            cache_write_tokens=TOKENS_MANUAL if primera else 0,
            cached_input_tokens=0 if primera else TOKENS_MANUAL,
            output_tokens=18,
            response_model=MODELO_BARATO,
            finish_reasons=["stop"],
        )
    return "Equipaje de mano incluido."


@laplace.observe(type="agent", name="agente_de_equipaje_arreglado")
def responder_sobre_equipaje_arreglado(pregunta: str, consultas: int = 6) -> str:
    for vuelta in range(consultas):
        consultar_manual_cacheado(pregunta, vuelta)
    return "Equipaje de mano incluido en todas las tarifas menos la básica."


@laplace.observe(type="agent", name="agente_extractor")
def responder_con_reintentos(pregunta: str) -> str:
    datos = extraer_datos(pregunta)
    vuelos = buscar_vuelos(datos["origen"], datos["destino"], "2026-10-14")
    return redactar(pregunta, vuelos, [])


@laplace.observe(type="agent", name="agente_de_viajes")
def responder_con_fallo(pregunta: str, usuario: str = "u-9") -> str:
    """Ejecución que revienta a mitad: la excepción queda en el span y en la traza."""
    preferencias = recuperar_preferencias(usuario)
    plan = planificar(pregunta, preferencias)
    vuelos = buscar_vuelos(plan["origen"], plan["destino"], plan["fecha"])
    reservar(vuelos[0]["vuelo"], usuario)  # lanza
    return "nunca se llega aquí"


# ---------------------------------------------------------------------------------


def main() -> int:
    endpoint = os.getenv("LAPLACE_ENDPOINT", "http://localhost:8000")
    laplace.init(
        project=os.getenv("LAPLACE_PROJECT", "demo-viajes"),
        endpoint=endpoint,
        service_name="agente-de-viajes",
        service_version="0.1.0",
    )
    print(f"enviando trazas a {endpoint}")

    preguntas = [
        "¿Qué vuelos hay de Madrid a Barcelona el 14 de octubre?",
        "Búscame un vuelo barato a Barcelona por la mañana",
        "Quiero volar a BCN el martes que viene",
    ]

    for i, pregunta in enumerate(preguntas, start=1):
        laplace.set_context(session_id=f"conv-{i}", user_id="u-7")
        respuesta = responder(pregunta)
        print(f"  [{i}] traza ok    — {respuesta[:60]}...")

    laplace.set_context(session_id="conv-larga", user_id="u-7")
    responder_iterando("Encuéntrame el vuelo más barato a Barcelona")
    print("  [4] traza larga — agente iterativo, ~30 pasos con un bucle")

    laplace.set_context(session_id="conv-reintentos", user_id="u-3")
    responder_con_reintentos("Vuelo de Madrid a Barcelona")
    print("  [5] traza con reintentos — la misma llamada al modelo tres veces")

    for i in range(1, 4):
        laplace.set_context(session_id=f"conv-equipaje-{i}", user_id="u-5")
        responder_sobre_equipaje("¿Cuánto equipaje puedo llevar?")
    print("  [6] contexto fijo — el manual entero reenviado en 18 llamadas sin caché")

    for i in range(1, 4):
        laplace.set_context(session_id=f"conv-equipaje-ok-{i}", user_id="u-5")
        responder_sobre_equipaje_arreglado("¿Cuánto equipaje puedo llevar?")
    print("  [7] el mismo agente ya cacheando — para comparar las dos facturas")

    laplace.set_context(session_id="conv-err", user_id="u-9")
    try:
        responder_con_fallo("Resérvame el primer vuelo que encuentres")
    except RuntimeError as exc:
        print(f"  [8] traza error — {exc}")

    laplace.flush()
    print("listo. Abre http://localhost:3000 para ver las trazas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
