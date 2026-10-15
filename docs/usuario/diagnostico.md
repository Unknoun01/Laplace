# Leer el Diagnóstico

## Las reglas

| Regla | Qué busca | Cómo calcula lo que se puede ahorrar |
|---|---|---|
| Repetición | El mismo paso, con la misma entrada, 3 veces o más en una ejecución | Lo que costaron las copias de sobra |
| Bucle | El mismo paso una y otra vez sin que su resultado avance | Lo que costaron las vueltas que no aportaron |
| Salida truncada | Una respuesta cortada por el tope de salida que el mismo paso vuelve a pedir | Lo que costó la respuesta cortada |
| JSON roto | Una respuesta con el JSON mal formado que el mismo paso vuelve a pedir | Lo que costó la respuesta que no se pudo leer |
| Historial que crece | La entrada de un paso que crece en cada turno de una conversación y no se recorta | Sólo tokens: no hay un tope que se pueda defender desde las trazas |
| Modelo caro | Un paso de respuesta corta con un modelo que tiene alternativa más barata | La diferencia de tarifa sobre los tokens reales |
| Contexto fijo | Un prompt con un prefijo grande que se reenvía sin caché, o cuya lectura de caché pesa en la factura | La diferencia entre tarifa normal y de caché, menos lo que cuesta escribirla; o lo que cuesta leerla |
| Caché compartida | El mismo prefijo en varios pasos de una ejecución, sin compartir la caché | Las lecturas que la caché podría servir, menos una escritura por ejecución |
| Prompt más caro | La versión de un prompt gestionado que cuesta más por ejecución que la anterior | La diferencia por ejecución, sobre el tráfico de la versión nueva |
| Por lotes | Llamadas que no esperan a ninguna otra, en ejecuciones que pueden esperar | Lo que costaron, por el descuento por lotes que publica el proveedor (la mitad en OpenAI y Anthropic) |
| Caché de respuestas | El mismo paso recibe la misma pregunta (salvo los números) en ejecuciones distintas y responde exactamente lo mismo | No entra en el total: dice «hasta» lo que costaron esas respuestas repetidas, porque no sabemos si la misma respuesta vale para el siguiente usuario. Compruébalo con `laplace replay` y una evaluación antes de cachear |

Dos reglas nunca reclaman el mismo dinero: lo que ya cuenta una, las demás lo
descuentan. El total de arriba es la suma, y se puede sumar.

### Marcar lo que puede esperar

Desde las trazas no se puede saber si alguien espera una respuesta. Si una ejecución
puede tardar horas —un proceso nocturno, una clasificación en masa—, márcala en su
función raíz y la regla de lotes te dirá cuánto ahorrarías:

```python
@laplace.observe(type="agent", tags=["laplace-puede-esperar"])
def proceso_de_fondo(...):
    ...
```

Sin la marca, la regla sólo pregunta, y sin poner dinero en el total: lo hace con las
ejecuciones que no llevan usuario ni sesión cuando el resto del proyecto sí los lleva.

## Qué quiere decir cada cifra

- **Lo que puedes dejar de pagar** es dinero ya gastado en el rango que miras, y su
  proyección a un mes. Con menos de un día de datos no se proyecta: se enseña lo gastado.
- **«Al menos 5 $»** quiere decir que falta algo en la cuenta, por ejemplo un modelo sin
  tarifa conocida: la cifra es un suelo, no un total.
- **«No lo sabemos»** es exactamente eso. Un modelo sin tarifa no cuesta cero; si usas
  uno, ponle tarifa en Ajustes.
- **Ya has dejado de pagar** suma lo que el seguimiento ha verificado en los problemas
  que marcaste como arreglados, por ejecución y no en totales.

## Cuánto entendemos

Arriba del todo, la cobertura dice qué parte de tu gasto pasa por llamadas que Laplace
entiende. Si es baja, lo dice antes que el dinero: una cifra sobre la mitad del tráfico
no es la de tu agente.

## Que Laplace proponga el arreglo

Si conectas un repositorio de GitHub en **Ajustes**, la ficha del modelo caro ofrece
**Abrir pull request**: Laplace cambia el modelo en la línea exacta de la llamada, en una
rama propia, y tú lo revisas y lo fusionas. Para eso el SDK anota desde qué fichero y
línea se llama al modelo (`capture_code_location=False` lo apaga). Si el modelo no está
escrito en el código —sale de una variable o de la configuración—, Laplace no adivina:
te dice dónde mirar.

Lo mismo con otros dos problemas, en Python:

- **Contexto fijo con Claude:** marca el `system` de la llamada con `cache_control`. Con
  OpenAI no hace falta, porque cachea solo. Si no se puede saber si el `system` es texto
  (sale de una función cualquiera), no lo toca y te dice por qué.
- **Un bucle:** pone `@laplace.guard(max_loop=4)` en la función que arranca la ejecución.
  Es un freno, no el arreglo de fondo: decide qué hacer cuando salte `GuardExceeded`.
  Para encontrar esa función, `@observe` anota dónde está definida.

Todo cambio tiene que dejar el fichero compilando, o no se propone.

**Al fusionar el PR, Laplace lo marca como arreglado** y comprueba si el problema baja.
Para que se entere, en GitHub (Settings → Webhooks) pon la URL y el secreto que salen en
**Ajustes**, con el evento *Pull requests*; con la GitHub App de tu instalación no hace
falta. Fusionar no es desplegar: si despliegas más tarde, vuelve a marcarlo entonces.
