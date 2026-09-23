"""El único sitio que sabe cómo se llama un paso cuando hay que enseñárselo a alguien.

Existe por una consecuencia de D-106 que tardó cuatro tandas en verse. Cuando la
identidad de un paso pasó a ser **el camino de llamada más la huella de sus
instrucciones**, dejó de ser cierto que `step_key` distinga lo que su nombre sugiere, y
dos pantallas se quedaron afirmando lo que ya no valía:

* El inicio enseñaba dos tarjetas con el título idéntico —«consultar_manual» da hasta 6
  vueltas sin avanzar— y cifras distintas, porque el título llevaba sólo el nombre de la
  función y las dos llamaban desde sitios distintos. Se leían como un duplicado.
* La pestaña de Prompts contaba «3 juegos de instrucciones» de un paso cuyo prompt no
  había cambiado nunca: lo que cambiaba era quién llamaba.

Las dos son el mismo fallo —`step_key` consumido con su significado anterior— y por eso
la respuesta es una sola función y no dos parches. Lo que cambie sobre cómo se nombra un
paso se cambia aquí, y las dos pantallas lo heredan.

La regla de redacción: **el nombre sólo crece cuando hace falta**. Un camino entero
—`atender_ticket > planificar > consultar_manual`— en un titular es ruido, así que se
enseña el último tramo y su llamante, que es lo mínimo que separa dos pasos homónimos.
"""

from __future__ import annotations

#: Separador con el que el SDK manda el camino de llamada.
SEPARADOR = " > "


def nombre_de_paso(label: str, site: str = "", *, con_llamante: bool = True) -> str:
    """Cómo se llama este paso en pantalla.

    `label` es el nombre de la función, que es lo que el usuario reconoce. `site` es el
    camino desde el que se llamó. Con `con_llamante`, el nombre lleva delante quien
    llama, que es lo único que distingue dos pasos que se llaman igual.

    Se devuelve el nombre a secas cuando no hay camino, cuando el camino es un único
    tramo —no hay llamante que añadir— o cuando el último tramo ya es el nombre y no hay
    nada que aclarar.
    """
    etiqueta = (label or "").strip()
    camino = [tramo.strip() for tramo in (site or "").split(SEPARADOR) if tramo.strip()]

    if not camino:
        return etiqueta
    hoja = camino[-1]
    if not etiqueta:
        etiqueta = hoja
    if not con_llamante or len(camino) < 2:
        return etiqueta

    llamante = camino[-2]
    # Si el llamante y el paso son el mismo texto, repetirlo no aclara nada.
    if llamante == etiqueta:
        return etiqueta
    return f"{llamante} → {etiqueta}"


#: Lo que se deja del prompt cuando hay que recurrir a él para separar dos pasos. El
#: recorte no es estética: sin él, el título de un hallazgo era el nombre de la función
#: más ochenta caracteres de prompt entre comillas dentro de otras comillas, y en la
#: tarjeta del inicio ocupaba dos líneas de las tres que tiene (D-115).
PISTA_EN_TITULO = 32


def con_pista(label: str, hint: str) -> str:
    """El último recurso: el paso más el principio de sus instrucciones, recortado.

    Se usa cuando no hay camino de llamada con el que separar dos pasos homónimos, que
    es el tráfico anterior a D-106 y el de quien no decora nada.
    """
    etiqueta = (label or "").strip()
    pista = " ".join((hint or "").split())
    if not pista or pista == etiqueta:
        return etiqueta
    if len(pista) > PISTA_EN_TITULO:
        pista = pista[:PISTA_EN_TITULO].rstrip(" ,.;:") + "…"
    return f"{etiqueta} — «{pista}»"


def hay_homonimos(pasos: list[tuple[str, str]]) -> set[str]:
    """De esos `(label, site)`, qué etiquetas aparecen con más de un camino.

    Sirve para decidir cuándo hace falta el llamante: si un nombre es único en el
    proyecto, añadirle de dónde viene es ruido; si no lo es, quitarlo convierte dos
    hallazgos distintos en un duplicado aparente.
    """
    caminos: dict[str, set[str]] = {}
    for label, site in pasos:
        caminos.setdefault((label or "").strip(), set()).add((site or "").strip())
    return {label for label, sitios in caminos.items() if len(sitios) > 1}


def identificador(nombre: str) -> str:
    """El nombre de la función, tal y como se escribe en código.

    Lo contrario de `nombre_de_paso` y `con_pista`: un título puede llevar el llamante
    —`agente → consultar_manual`— o una pista del prompt, y los dos son ruido, o
    directamente código inválido, dentro de un fragmento de Python que el usuario va a
    copiar. Vive aquí porque es el único sitio que sabe cómo se decora un nombre.
    """
    base = (nombre or "").split(" → ")[-1]
    base = base.split(" — «")[0].strip()
    return base or "paso"
