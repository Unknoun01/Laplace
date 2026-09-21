"""El mismo trabajo que `agente_mediocre_ollama.py`, escrito bien.

Existe para contestar una pregunta que el agente mediocre por sí solo no puede
contestar: **¿los hallazgos señalan al culpable, o le salen a cualquiera?** Si Laplace
acusara también a éste, sus reglas no valdrían para nada.

Atiende los mismos tickets, contra los mismos pedidos, con los mismos dos modelos. Lo
único que cambia es cómo está escrito:

| El mediocre hace…                              | …y éste                                      |
|------------------------------------------------|----------------------------------------------|
| Clasifica tres veces «por si acaso»             | Una vez                                      |
| Consulta el pedido tres veces, con el mismo id  | Una vez, y se pasa el resultado              |
| Usa el modelo grande para decir «es»            | Detecta el idioma sin modelo, mirando el texto |
| Manda la política **y el catálogo** (~3.000 tok)| Manda las siete reglas que hacen falta       |
| Sondea el almacén seis veces sin salir nunca    | Pregunta una vez y se cree la respuesta      |
| Mete la hora con segundos en el prompt de sistema | Instrucciones fijas; la fecha va en el mensaje |

Y usa el modelo grande **donde sí hace falta**: para redactar la respuesta al cliente.
Ése es el punto que conviene ver en pantalla: no es «modelo pequeño bueno, grande malo»,
es cada uno en su sitio.

    python examples/agente_sano_ollama.py --minutos 10

Variables: las mismas que el mediocre (ver `_tienda.py`). Por defecto los dos escriben
en el mismo proyecto, que es lo que permite compararlos en la misma pantalla.
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

# ---------------------------------------------------------------------------------
# Herramientas. Las mismas que el otro agente, con los mismos nombres a propósito:
# así, si un hallazgo nombra «consultar_pedido», se ve que señala a un paso y no a una
# función suelta, y que la identidad de un paso es desde dónde se llama (D-060).
# ---------------------------------------------------------------------------------


@laplace.observe(type="tool")
def consultar_pedido(pedido_id: str) -> dict:
    time.sleep(0.15)
    return t.PEDIDOS.get(pedido_id, {"error": "pedido no encontrado"})


@laplace.observe(type="tool")
def consultar_stock(sku: str) -> int:
    time.sleep(0.1)
    return t.STOCK.get(sku, 0)


@laplace.observe(type="tool")
def estado_almacen(pedido_id: str) -> str:
    time.sleep(0.2)
    return "listo" if t.PEDIDOS.get(pedido_id, {}).get("estado") == "en almacén" else "en curso"


# ---------------------------------------------------------------------------------
# Pasos
# ---------------------------------------------------------------------------------


@laplace.observe()
def entender_ticket(ticket: str) -> dict:
    """Una sola llamada al modelo pequeño para intención y pedido a la vez.

    El mediocre gasta cuatro llamadas en esto mismo: tres de clasificación idéntica más
    una de extracción. El número de pedido además se confirma con una expresión regular,
    que es exacta y gratis, en vez de fiarlo al modelo.
    """
    intencion = t.llamar(
        t.PEQUENO,
        "Clasifica el mensaje del cliente en una sola palabra: "
        "envio, devolucion, defecto, cambio, cobro u otro.",
        ticket,
        max_tokens=5,
    ).lower()
    encontrado = re.search(r"PED-\d{4}", ticket)
    return {"intencion": intencion, "pedido_id": encontrado.group(0) if encontrado else ""}


def idioma(ticket: str) -> str:
    """Sin modelo y sin span: tres marcadores bastan y cuestan cero.

    Que esto no lleve `@observe` es deliberado. No es un paso del agente, es una función
    de tres líneas; decorar cada utilidad llenaría el árbol de ruido y no añadiría nada.
    """
    bajo = f" {ticket.lower()} "
    if any(p in bajo for p in (" my ", " order ", " help", " shows ")):
        return "en"
    if any(p in bajo for p in (" ma ", " commande ", "bonjour")):
        return "fr"
    return "es"


@laplace.observe()
def redactar_respuesta(ticket: str, contexto: dict) -> str:
    """El modelo grande, que aquí sí hace falta: esto lo lee un cliente.

    Las instrucciones son las siete reglas, no el catálogo entero. El pedido ya viene
    resuelto de antes, así que no se vuelve a consultar.
    """
    return t.llamar(
        t.GRANDE,
        f"Eres el agente de soporte de una tienda de montaña.\n{t.REGLAS}\n"
        "Contesta al cliente en su idioma, en tres frases como mucho.",
        f"Idioma: {contexto['idioma']}. Intención: {contexto['intencion']}. "
        f"Pedido: {contexto['pedido']}. Unidades en stock: {contexto['stock']}. "
        f"Estado en almacén: {contexto['almacen']}.\n\nMensaje: {ticket}",
        max_tokens=120,
    )


@laplace.observe()
def resumir_para_crm(ticket: str, numero: int) -> str:
    """Instrucciones fijas. La fecha y el número van en el **mensaje**, no en ellas.

    Es la diferencia exacta con `resumir_para_crm` del agente mediocre, y la razón de
    que a éste la cobertura no lo señale como paso partido: las instrucciones son las
    mismas en cada ejecución, así que el paso mantiene una sola identidad (D-060).
    """
    ahora = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return t.llamar(
        t.PEQUENO,
        "Estás archivando un ticket en el CRM. Resume el problema en diez palabras.",
        f"Fecha: {ahora}. Ticket: {numero}.\n\n{ticket}",
        max_tokens=24,
    )


@laplace.observe(type="agent")
def responder_consulta(ticket: str, numero: int) -> str:
    entendido = entender_ticket(ticket)
    pedido_id = entendido["pedido_id"]
    pedido = consultar_pedido(pedido_id)
    contexto = {
        "intencion": entendido["intencion"],
        "idioma": idioma(ticket),
        "pedido": pedido,
        "stock": consultar_stock(pedido.get("sku", "")),
        "almacen": estado_almacen(pedido_id),
    }
    respuesta = redactar_respuesta(ticket, contexto)
    resumir_para_crm(ticket, numero)
    return respuesta


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
    parser.add_argument("--ejecuciones", type=int, default=None)
    parser.add_argument("--pausa", type=float, default=2.0)
    args = parser.parse_args()
    _salida_utf8()

    if not t.comprobar_entorno():
        return 1

    laplace.init(project=t.PROYECTO, endpoint=t.LAPLACE_ENDPOINT, service_name="soporte-sano")
    print(f"[sano] proyecto «{t.PROYECTO}» → {t.LAPLACE_ENDPOINT}")

    fin = time.monotonic() + args.minutos * 60
    numero = 0
    try:
        while time.monotonic() < fin and (args.ejecuciones is None or numero < args.ejecuciones):
            numero += 1
            ticket = random.choice(t.TICKETS)
            laplace.set_context(session_id=f"sano-{numero}", user_id=ticket.split()[0])
            inicio = time.monotonic()
            try:
                responder_consulta(ticket, numero)
                resultado = "ok"
            except Exception as exc:  # noqa: BLE001 - un ticket roto no para la tanda
                resultado = f"error: {type(exc).__name__}"
            print(f"[sano] [{numero:3d}] {time.monotonic() - inicio:5.1f} s {resultado:<8} "
                  f"{ticket[:50]}")
            laplace.flush()
            time.sleep(args.pausa)
    except KeyboardInterrupt:
        print("[sano] parado a mano.")

    laplace.flush()
    print(f"[sano] listo: {numero} tickets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
