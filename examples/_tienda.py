"""El mundo que comparten los dos agentes de ejemplo: una tienda de montaña.

Vive aparte por dos motivos. Uno práctico: `agente_mediocre_ollama.py` y
`agente_sano_ollama.py` tienen que trabajar contra **los mismos datos y los mismos
tickets**, o comparar lo que dice Laplace de cada uno no significaría nada. Y otro de
honestidad: esto es la base de datos de la tienda y su catálogo, no trazas. Lo que se
inventa es el mundo; el tráfico sale de ejecutar los agentes de verdad.

Aquí no hay ninguna patología. Los vicios están en los agentes, cada uno en su función.
"""

from __future__ import annotations

import os

import openai

LAPLACE_ENDPOINT = os.getenv("LAPLACE_ENDPOINT", "http://127.0.0.1:8100")
PROYECTO = os.getenv("LAPLACE_PROJECT", "agente-mediocre")
OLLAMA = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
PEQUENO = os.getenv("LAPLACE_MODELO_PEQUENO", "qwen2.5:0.5b")
GRANDE = os.getenv("LAPLACE_MODELO_GRANDE", "qwen2.5:1.5b")

#: Lo que ocupa cada modelo en disco, para decirlo antes de pedir que se descargue.
TAMANOS = {"qwen2.5:0.5b": "~400 MB", "qwen2.5:1.5b": "~1 GB", "llama3.2:1b": "~1,3 GB"}

PEDIDOS = {
    "PED-1042": {"cliente": "Marta", "sku": "MOCH-20", "estado": "enviado", "dias": 6},
    "PED-1043": {"cliente": "Iker", "sku": "BOT-INOX", "estado": "en almacén", "dias": 2},
    "PED-1051": {"cliente": "Lucía", "sku": "TIEN-2P", "estado": "entregado", "dias": 9},
    "PED-1060": {"cliente": "Samuel", "sku": "SAC-0G", "estado": "en almacén", "dias": 1},
    "PED-1077": {"cliente": "Nerea", "sku": "LINT-FR", "estado": "enviado", "dias": 4},
    "PED-1089": {"cliente": "Pablo", "sku": "BAST-CARB", "estado": "devuelto", "dias": 15},
}

STOCK = {"MOCH-20": 4, "BOT-INOX": 0, "TIEN-2P": 2, "SAC-0G": 7, "LINT-FR": 12, "BAST-CARB": 1}

TICKETS = [
    "Hola, mi pedido PED-1042 lleva 6 días y no llega. ¿Dónde está?",
    "Buenas, quiero devolver la botella del pedido PED-1043, ha llegado abollada.",
    "La tienda de campaña del PED-1051 tiene una varilla rota. ¿Me mandáis otra?",
    "¿Podéis cambiar la dirección de envío del pedido PED-1060? Me mudo el lunes.",
    "Hello, my order PED-1077 says shipped but tracking shows nothing. Help?",
    "Devolví los bastones del PED-1089 hace dos semanas y no veo el reembolso.",
    "Pedido PED-1043: ¿cuándo sale del almacén? Lo necesito para el viernes.",
    "Bonjour, ma commande PED-1042 n'est toujours pas arrivée.",
    "¿El saco del PED-1060 aguanta bajo cero? Si no, lo cancelo antes de que salga.",
    "Me han cobrado dos veces el pedido PED-1077. Quiero que me devolváis uno.",
]

#: Las siete reglas que de verdad hacen falta para contestar. El agente sano manda esto.
REGLAS = (
    "1. Devoluciones: 30 días desde la entrega, producto sin usar y con etiqueta.\n"
    "2. Reembolsos: se emiten en 5 a 10 días hábiles tras recibir la devolución.\n"
    "3. Envíos: peninsular 48-72 h; islas 5-7 días; internacional 7-15 días.\n"
    "4. Defectos de fábrica: sustitución sin coste durante 2 años de garantía.\n"
    "5. Cambios de dirección: sólo mientras el pedido esté en almacén.\n"
    "6. Cobros duplicados: se anulan en 72 h; nunca pedir datos de tarjeta.\n"
    "7. Tono: cercano, breve, sin prometer fechas que no dependan de nosotros."
)

#: Las reglas **más el catálogo entero**, unos 3.000 tokens. El agente mediocre manda
#: esto en cada llamada; el sano no lo usa. La diferencia entre los dos ficheros es, en
#: buena parte, esta constante.
CATALOGO = "\n".join(
    f"Artículo {i:03d} ({cat}): {nombre} modelo {m}. Precio {10 + i * 3} €. "
    f"Peso {100 + i * 17} g. Garantía 2 años. Tallas S, M y L cuando aplica. "
    f"Cuidado: limpiar con agua tibia y secar a la sombra."
    for i, (cat, nombre, m) in enumerate(
        (c, n, m)
        for c, n in [
            ("mochilas", "Mochila de travesía"),
            ("hidratación", "Botella térmica"),
            ("tiendas", "Tienda ligera"),
            ("sacos", "Saco de plumas"),
            ("iluminación", "Linterna frontal"),
            ("bastones", "Bastón telescópico"),
        ]
        for m in ("Alfa", "Beta", "Gamma", "Delta", "Épsilon", "Zeta")
    )
)

POLITICA = f"POLÍTICA DE ATENCIÓN AL CLIENTE — TIENDA DE MONTAÑA\n{REGLAS}\n{CATALOGO}"

#: El cliente **real** de OpenAI apuntando a Ollama. Laplace lo instrumenta solo en
#: cuanto el agente llama a `laplace.init()`: no hay nada que envolver a mano.
cliente = openai.OpenAI(base_url=OLLAMA, api_key="ollama-no-mira-la-clave", max_retries=0)


def llamar(modelo: str, sistema: str, usuario: str, max_tokens: int) -> str:
    """Una llamada al modelo, tal cual la escribiría cualquiera."""
    respuesta = cliente.chat.completions.create(
        model=modelo,
        messages=[
            {"role": "system", "content": sistema},
            {"role": "user", "content": usuario},
        ],
        temperature=0,
        max_tokens=max_tokens,
    )
    return (respuesta.choices[0].message.content or "").strip()


def comprobar_entorno(modelos: tuple[str, ...] = (PEQUENO, GRANDE)) -> bool:
    """Que Ollama y los modelos estén, sin descargar nada por nuestra cuenta."""
    import httpx

    try:
        disponibles = {m["id"] for m in httpx.get(f"{OLLAMA}/models", timeout=3).json()["data"]}
    except Exception:  # noqa: BLE001
        print(f"No hay Ollama escuchando en {OLLAMA}. Arráncalo (o instálalo) y repite.")
        return False
    faltan = [m for m in modelos if m not in disponibles]
    for modelo in faltan:
        print(f"Falta el modelo {modelo} ({TAMANOS.get(modelo, 'tamaño desconocido')}):")
        print(f"    ollama pull {modelo}")
    if faltan:
        return False
    try:
        httpx.get(f"{LAPLACE_ENDPOINT}/health", timeout=3).raise_for_status()
    except Exception:  # noqa: BLE001
        # Antes esto era un aviso y se seguía igual. No sirve de nada: el agente tarda
        # minutos en correr y al final no hay trazas que mirar, que es lo único que se
        # venía a hacer. Mejor no empezar.
        print(f"Laplace no responde en {LAPLACE_ENDPOINT}, así que las trazas se perderían.")
        print("Arráncalo en otra terminal, desde la raíz del repositorio:")
        print(r"    .\.venv\Scripts\python.exe -m laplace.cli ui")
        print("(o apunta a otro con LAPLACE_ENDPOINT)")
        return False
    return True
