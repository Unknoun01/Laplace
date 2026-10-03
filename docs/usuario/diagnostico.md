# Leer el Diagnóstico

## Las cinco reglas

| Regla | Qué busca | Cómo calcula lo que se puede ahorrar |
|---|---|---|
| Repetición | El mismo paso, con la misma entrada, 3 veces o más en una ejecución | Lo que costaron las copias de sobra |
| Bucle | El mismo paso una y otra vez sin que su resultado avance | Lo que costaron las vueltas que no aportaron |
| Modelo caro | Un paso de respuesta corta con un modelo que tiene alternativa más barata | La diferencia de tarifa sobre los tokens reales |
| Contexto fijo | Un prompt con un prefijo grande que se reenvía sin caché | La diferencia entre tarifa normal y de caché, menos lo que cuesta escribirla |
| Prompt más caro | La versión de un prompt gestionado que cuesta más por ejecución que la anterior | La diferencia por ejecución, sobre el tráfico de la versión nueva |

Dos reglas nunca reclaman el mismo dinero: lo que ya cuenta una, las demás lo
descuentan. El total de arriba es la suma, y se puede sumar.

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
