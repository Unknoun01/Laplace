"""Un agente de soporte **deliberadamente mediocre**, corriendo de verdad contra Ollama.

Existe para ver Laplace trabajando con tráfico real y, sobre todo, para ver qué NO ve.
Si el agente estuviera bien escrito, Laplace no encontraría nada y no se aprendería
nada. Aquí cada vicio está puesto a propósito, en su propia función y con su etiqueta,
para poder cruzar lo que hace el código con lo que dice la pantalla.

Nada de datos sembrados: cada traza sale de ejecutar el agente, con llamadas reales a
modelos reales por la API de OpenAI que sirve Ollama. Lo único inventado es el mundo en
el que trabaja —los correos de clientes, la tabla de pedidos y el catálogo—, igual que
un agente real trabaja contra la base de datos de su empresa.

## Lo que hace, en una ejecución (= una traza)

    atender_ticket                                         agente
    ├─ clasificar_intencion      ×3  modelo pequeño       P1  repetición exacta (LLM)
    ├─ detectar_idioma               modelo GRANDE        P2  modelo caro, paso trivial
    ├─ extraer_pedido                modelo pequeño
    │   └─ consultar_pedido          herramienta          P1  repetición exacta (tool)
    ├─ consultar_stock               herramienta
    ├─ esperar_confirmacion_almacen  bucle ×6             P4  bucle atascado
    │   ├─ estado_almacen            herramienta
    │   └─ (decidir si seguir)       modelo pequeño
    ├─ redactar_respuesta            modelo GRANDE        P3  contexto largo sin caché
    │   └─ consultar_pedido          herramienta          P1  (segunda vez)
    ├─ revisar_respuesta             modelo pequeño
    │   └─ consultar_pedido          herramienta          P1  (tercera vez)
    └─ resumir_para_crm              modelo pequeño       P5  fecha en el prompt de sistema

## Las patologías, y cómo reconocerlas

P1 · Repetición exacta. `clasificar_intencion` pregunta tres veces lo mismo al modelo
     «por si acaso», con temperatura 0: las tres respuestas son idénticas. Y
     `consultar_pedido` se llama tres veces con el mismo id en la misma traza, porque
     cada paso la pide por su cuenta en vez de pasarse el resultado.
P2 · Modelo caro para un paso trivial. `detectar_idioma` usa el modelo grande para
     contestar una palabra.
P3 · Contexto largo reenviado sin caché. `redactar_respuesta` pega en cada llamada la
     política entera y el catálogo completo (~2.500 tokens), sin marcar nada cacheable.
P4 · Bucle atascado. `esperar_confirmacion_almacen` sondea el almacén hasta que diga
     «listo»; el almacén dice «Listo para envío» y la comparación es `== "listo"`, así
     que no sale nunca por ahí y agota los seis intentos. Cada vuelta lleva el número de
     intento, así que **ninguna llamada es idéntica a otra**.
P5 · Dato variable en el prompt de sistema. `resumir_para_crm` pone la fecha y hora con
     segundos en las instrucciones. Cada ejecución estrena instrucciones, así que el
     paso se parte en tantas identidades como ejecuciones.

## Cómo lanzarlo

    python examples/agente_mediocre_ollama.py                  # 10 minutos seguidos
    python examples/agente_mediocre_ollama.py --ejecuciones 3  # sólo tres tickets
    python examples/agente_mediocre_ollama.py --minutos 20

Variables:
    LAPLACE_ENDPOINT        por defecto http://127.0.0.1:8100 (el de `laplace ui`)
    LAPLACE_PROJECT         por defecto agente-mediocre
    OLLAMA_BASE_URL         por defecto http://localhost:11434/v1
    LAPLACE_MODELO_PEQUENO  por defecto qwen2.5:0.5b
    LAPLACE_MODELO_GRANDE   por defecto qwen2.5:1.5b
"""

from __future__ import annotations

import argparse
import contextlib
import random
import re
import sys
import time
from datetime import datetime

import _tienda as t
import laplace

#: El mundo de la tienda —pedidos, stock, tickets, catálogo— y la llamada al modelo
#: viven en `_tienda.py`, compartidos con `agente_sano_ollama.py`. Los dos agentes
#: tienen que trabajar contra los mismos datos, o compararlos no diría nada.
INTENTOS_ALMACEN = 6


# ---------------------------------------------------------------------------------
# Herramientas
# ---------------------------------------------------------------------------------


@laplace.observe(type="tool")
def consultar_pedido(pedido_id: str) -> dict:
    """P1 · Se llama tres veces por traza con el mismo id: nadie pasa el resultado."""
    time.sleep(0.15)  # una base de datos de verdad tampoco es instantánea
    return t.PEDIDOS.get(pedido_id, {"error": "pedido no encontrado"})


@laplace.observe(type="tool")
def consultar_stock(sku: str) -> int:
    time.sleep(0.1)
    return t.STOCK.get(sku, 0)


@laplace.observe(type="tool")
def estado_almacen(pedido_id: str, intento: int) -> str:
    """El almacén contesta bien; es quien pregunta el que compara mal (P4)."""
    time.sleep(0.2)
    return "Listo para envío" if intento >= 2 else "pendiente de preparar"


# ---------------------------------------------------------------------------------
# Pasos del agente
# ---------------------------------------------------------------------------------


@laplace.observe(tags=["P1-repeticion-llm"])
def clasificar_intencion(ticket: str) -> str:
    """P1 · Tres votos idénticos: temperatura 0, mismo prompt, misma respuesta."""
    votos = [
        t.llamar(
            t.PEQUENO,
            "Clasifica el mensaje del cliente en una sola palabra: "
            "envio, devolucion, defecto, cambio, cobro u otro.",
            ticket,
            max_tokens=5,
        )
        for _ in range(3)
    ]
    return max(set(votos), key=votos.count).lower()


@laplace.observe(tags=["P2-modelo-caro"])
def detectar_idioma(ticket: str) -> str:
    """P2 · El modelo grande para contestar «es», «en» o «fr»."""
    return t.llamar(
        t.GRANDE,
        "Di el idioma del texto con su código de dos letras. Sólo el código.",
        ticket,
        max_tokens=3,
    ).lower()[:2]


@laplace.observe()
def extraer_pedido(ticket: str) -> dict:
    texto = t.llamar(
        t.PEQUENO,
        "Extrae el número de pedido del mensaje. Responde sólo el número, como PED-1234.",
        ticket,
        max_tokens=8,
    )
    encontrado = re.search(r"PED-\d{4}", texto) or re.search(r"PED-\d{4}", ticket)
    pedido_id = encontrado.group(0) if encontrado else ""
    return {"pedido_id": pedido_id, **consultar_pedido(pedido_id)}


@laplace.observe(tags=["P4-bucle-atascado"])
def esperar_confirmacion_almacen(pedido_id: str) -> bool:
    """P4 · Sondea hasta que el almacén diga «listo». Nunca lo reconoce."""
    for intento in range(INTENTOS_ALMACEN):
        estado = estado_almacen(pedido_id, intento)
        if estado == "listo":  # el almacén dice «Listo para envío»: no casa nunca
            return True
        t.llamar(
            t.PEQUENO,
            "Eres el coordinador del almacén. Responde SEGUIR o PARAR.",
            f"Intento {intento + 1} de {INTENTOS_ALMACEN}. Estado del pedido {pedido_id}: "
            f"{estado}. ¿Seguimos esperando?",
            max_tokens=4,
        )
    return False


@laplace.observe(tags=["P3-contexto-sin-cache"])
def redactar_respuesta(ticket: str, pedido_id: str, intencion: str, idioma: str) -> str:
    """P3 · La política y el catálogo enteros, en cada llamada, sin caché."""
    pedido = consultar_pedido(pedido_id)  # P1, segunda vez
    return t.llamar(
        t.GRANDE,
        f"Eres el agente de soporte de una tienda de montaña.\n\n{t.POLITICA}\n\n"
        "Contesta al cliente en su idioma, en tres frases como mucho.",
        f"Idioma: {idioma}. Intención: {intencion}. Pedido: {pedido}.\n\nMensaje: {ticket}",
        max_tokens=120,
    )


@laplace.observe()
def revisar_respuesta(borrador: str, pedido_id: str) -> str:
    pedido = consultar_pedido(pedido_id)  # P1, tercera vez
    return t.llamar(
        t.PEQUENO,
        "Revisa si la respuesta al cliente contradice los datos del pedido. "
        "Responde OK o describe el problema en una frase.",
        f"Pedido: {pedido}\n\nRespuesta: {borrador}",
        max_tokens=30,
    )


@laplace.observe(tags=["P5-fecha-en-el-sistema"])
def resumir_para_crm(ticket: str, numero: int) -> str:
    """P5 · La hora con segundos en las instrucciones: una identidad por ejecución."""
    ahora = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return t.llamar(
        t.PEQUENO,
        f"Hoy es {ahora}. Estás archivando el ticket número {numero} en el CRM. "
        "Resume el problema del cliente en diez palabras.",
        ticket,
        max_tokens=24,
    )


@laplace.observe(type="agent")
def atender_ticket(ticket: str, numero: int) -> str:
    intencion = clasificar_intencion(ticket)
    idioma = detectar_idioma(ticket)
    pedido = extraer_pedido(ticket)
    pedido_id = pedido.get("pedido_id", "")
    consultar_stock(pedido.get("sku", ""))
    esperar_confirmacion_almacen(pedido_id)
    borrador = redactar_respuesta(ticket, pedido_id, intencion, idioma)
    revisar_respuesta(borrador, pedido_id)
    resumir_para_crm(ticket, numero)
    return borrador


# ---------------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------------


def _salida_utf8() -> None:
    """Windows escribe a una tubería en cp1252 y revienta con «→» o ««»».

    Pasa sólo cuando la salida NO es una consola —redirigida a un fichero, o leída por
    `demo_en_vivo.py`—, que es justo como se lanza esto. Un acento no puede tumbar un
    agente de ejemplo.
    """
    for flujo in (sys.stdout, sys.stderr):
        # Un flujo sin `reconfigure` (Python viejo, salida redirigida de forma rara) no
        # es motivo para no arrancar.
        with contextlib.suppress(Exception):
            flujo.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutos", type=float, default=10.0)
    parser.add_argument("--ejecuciones", type=int, default=None, help="corta tras N tickets")
    parser.add_argument("--pausa", type=float, default=2.0, help="segundos entre tickets")
    args = parser.parse_args()
    _salida_utf8()

    if not t.comprobar_entorno():
        return 1

    laplace.init(
        project=t.PROYECTO, endpoint=t.LAPLACE_ENDPOINT, service_name="soporte-mediocre"
    )
    print(f"[mediocre] proyecto «{t.PROYECTO}» → {t.LAPLACE_ENDPOINT}")
    print(f"[mediocre] modelos: pequeño {t.PEQUENO} · grande {t.GRANDE}")
    limite = f"{args.ejecuciones} tickets" if args.ejecuciones else f"{args.minutos:g} minutos"
    print(f"Atendiendo tickets durante {limite}. Ctrl+C para parar.\n")

    fin = time.monotonic() + args.minutos * 60
    numero = 0
    try:
        while time.monotonic() < fin and (args.ejecuciones is None or numero < args.ejecuciones):
            numero += 1
            ticket = random.choice(t.TICKETS)
            laplace.set_context(session_id=f"ticket-{numero}", user_id=ticket.split()[0])
            inicio = time.monotonic()
            try:
                atender_ticket(ticket, numero)
                resultado = "ok"
            except Exception as exc:  # noqa: BLE001 - un ticket roto no para la tanda
                resultado = f"error: {type(exc).__name__}: {exc}"
            print(f"  [{numero:3d}] {time.monotonic() - inicio:5.1f} s  {resultado:<8} {ticket[:60]}")
            laplace.flush()
            time.sleep(args.pausa)
    except KeyboardInterrupt:
        print("\nParado a mano.")

    laplace.flush()
    print(f"\nListo: {numero} tickets. Míralos en {t.LAPLACE_ENDPOINT}/?project={t.PROYECTO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
