# Estado de Laplace

Última actualización: 8 de septiembre de 2026.

## Dónde está el producto

Laplace observa agentes de IA y dice cuánto cuestan y cuánto pueden dejar de costar. Ese
ciclo —observar, diagnosticar, ahorrar, avisar, vigilar— está **cerrado y funcionando de
punta a punta** por los dos caminos: la instalación de nube con Docker y el modo local de
un solo proceso. Un agente instrumentado con una línea de Python emite trazas por OTLP,
la ingesta les calcula el coste por tramos de tokens, tres reglas deterministas
encuentran el derroche, la interfaz lo cuenta en dinero con el cálculo detrás, una alerta
a Slack avisa cuando algo pasa del umbral, y el panel dice si el gasto sube porque hay
más trabajo o porque el trabajo se ha encarecido. **Las fases 2 y 4.a quedan cerradas.**
Lo que falta es el diagnóstico automático con modelo y las pestañas de evaluación y
prompts, que son fases enteras y no remates.

## Hecho

| Fase | Qué es | Estado |
|------|--------|--------|
| 0 | Monorepo, `docker-compose`, contrato de traza, esquemas | ✅ |
| 1.4 | SDK de Python: `@observe`, auto-instrumentación, streaming | ✅ |
| 1.5 | Ingesta OTLP + API de lectura | ✅ |
| 1.6 | Explorador de trazas + vista de árbol | ✅ |
| 1.7 | Modo local `laplace ui` con SQLite | ✅ |
| 2 | Detección de derroche y panel de ahorro | ✅ |
| 2.b | Alertas a Slack por umbral, con periodo de calma | ✅ |
| 4.a | Panel por unidad de trabajo, atribución de picos y modo en vivo | ✅ |

Lo que sostiene esas casillas, en concreto:

- **El modelo de coste cobra por tramos**: entrada nueva, lectura de caché, escritura de
  caché, y encima los metros que pida la petición (lote, modo rápido, residencia de
  datos). Los precios salen de las páginas oficiales con URL y fecha de verificación.
- **Un modelo sin tarifa no cuesta cero**: cuesta «no lo sabemos», y la interfaz dice que
  el total está incompleto. Lo mismo con los metros que no se pueden determinar: se cobra
  el estándar y la cifra se presenta como un suelo.
- **La identidad de un paso no es su nombre**, sino desde dónde se llama y con qué
  instrucciones. Sin eso, las reglas mezclaban pasos distintos en cualquier código real.
- **El modo local es el mismo producto**, no una versión recortada: hay un test que
  compara los dos almacenes sobre los mismos spans y exige los mismos hallazgos con el
  mismo dinero, y otro que exige la misma decisión de alerta.
- **No se proyecta un mes desde una hora de datos.** Por debajo de un día observado, el
  panel enseña el gasto real con su ventana —«te ha costado 0,65 $ en menos de un minuto
  de datos»— y dice cuánto falta para que aparezca la previsión. La cifra mensual llega
  como `null`, no como cero, para que nadie la pinte como «no cuesta nada» (D-073).
- **Las alertas están escritas contra el ruido**, no contra el silencio: un mensaje por
  proyecto, periodo de calma por hallazgo, y nada de «volvemos a avisar porque ha
  subido». Umbral, silencio y reglas silenciadas se configuran por proyecto (D-074).
- **El panel mide por unidad de trabajo y dice la lectura con palabras.** «Subida
  acompañada de más ejecuciones: normal» frente a «Subida sin más ejecuciones: revisar»,
  con las tres cifras que sostienen la frase debajo. Los totales van en letra pequeña
  porque por sí solos no dicen si algo va mal (D-077).
- **Un pico se atribuye con datos o se dice que no se sabe.** Modelo nuevo, herramienta
  nueva, paso nuevo o paso que se lleva el sobrecoste, todo comprobable en las trazas y
  con enlace al tramo exacto en el explorador. Nunca una correlación insinuada (D-078).
- **162 pruebas, y ya no se salta ninguna.** Se levantó ClickHouse y se ejecutaron las 31
  que llevaban tiempo saltándose: el SQL de la nube está probado, y la paridad entre los
  dos almacenes cubre ahora los hallazgos, las marcas de suelo, la decisión de alerta y
  el panel entero.

## Qué queda

En el orden acordado:

1. **Pestaña Evaluaciones.** Anotación humana, LLM-as-judge claramente separado en la
   interfaz y en los datos, conjuntos de casos a partir de tráfico real, y la comparación
   A vs B de acierto y coste a la vez, que es lo valioso.
2. **Pestaña Prompts.** Versiones con diff, cuál está en producción, rollback en un clic,
   y las métricas reales de cada versión pegadas a ella.

Los huecos de las fases 3 y 4 ya existen en el contrato, en el esquema de Postgres y en
la API: devuelven `null` y listas vacías, así que cuando lleguen no hay migración. La
atribución de picos tiene reservado su tipo de causa para la versión de prompt: el día
que exista la Fase 5, entra sin tocar nada más.

## Qué hay que vigilar

**Los precios caducan, y rápido.** `model_prices.json` lleva fecha de verificación y hay
que reverificarlo **cada 30 días**. Tres tests fallan solos: cuando una fuente pasa de 30
días, cuando vence una tarifa promocional, y cuando aparece en las trazas un modelo que
no está en la tabla. No los silencies: cada uno significa que alguna cifra en pantalla ha
dejado de ser cierta.

**El doble conteo es el fallo recurrente de este proyecto.** Ha aparecido por cuatro
caminos distintos: dos reglas sobre los mismos tokens, dos reglas sobre el mismo paso, el
cruce del descuento sin pareja al cambiar la clave de agrupación, y el descuento de
tokens sin descontar llamadas. Cada uno dejó su caso en `test_insights.py`. Reglas al
tocar el motor: los casos se añaden, nunca se sustituyen; las cifras se comprueban
exactas y no con un tope contra el gasto total, que deja pasar el error mientras quepa
dentro; y hay que comprobar que los tests muerden rompiendo el motor a propósito. **El
test de regresión del solape no se toca.**

**La identidad de un paso puede partirse.** Un prompt de sistema con datos variables
—una fecha, un nombre— genera una huella distinta por llamada y parte un paso en muchos.
Entonces las reglas se callan por falta de llamadas, que es el lado seguro, pero el
usuario no ve nada. Si aparece en datos reales, la solución es normalizar la huella, no
volver a agrupar por nombre.

**La proyección tiene una puerta, y hay que cruzarla en un solo sitio.** Gasto y ahorro
salen siempre de la misma base y ahora también comparten la decisión de proyectar o no;
la toma `_projection_base()`, y sólo ella. Cualquier cifra nueva que se extrapole tiene
que pedírsela: proyectar un total y no su ahorro (o al revés) es la misma mentira de
D-058 con otra cara, y la barra de reparto del inicio la enseñaría sin pestañear.

**El panel se rompe volviéndose genérico.** Existe por dos ideas —coste por unidad de
trabajo y atribución de picos con datos reales— y sin ellas no vale la pena mantenerlo.
Dos tentaciones concretas a las que no ceder: añadir series de totales «porque están»
(cada una que se sube al mismo nivel que las de por ejecución diluye el mensaje), y
rellenar la causa de un pico con algo que correlacione. «No identificamos la causa» es
una respuesta del producto, no un fallo del producto.

**Comparar con un periodo casi vacío es la misma mentira que proyectar sobre una hora.**
En la primera prueba del panel salió «el gasto sube un 193.100 %». Lo tapan las dos
guardas de `comparable()`, y cualquier comparación nueva —contra la semana pasada, contra
otro proyecto, contra una versión de prompt— tiene que pasar por una guarda equivalente
antes de enseñar un porcentaje.

**Las alertas se rompen por ruido, no por silencio.** Todo lo que las hace útiles vive en
`decide()`, que no toca ni la red ni el reloj y por eso se puede probar entero. Si
alguien añade ahí un motivo nuevo para volver a mandar un mensaje —que ha empeorado, que
hay uno nuevo del mismo tipo, que el usuario no ha entrado— habrá reinventado la alerta
que se repite. La regla es que el único motivo para reabrir el silencio es que se acabe
el periodo de calma. **Y el estado se persiste a propósito:** con el estado en memoria,
un backend que se reinicie en bucle vuelve a avisar de todo en cada arranque.

**El modo local puede derivar del de nube.** El test de paridad lo protege, pero sólo
compara lo que compara: si añades un campo al almacén, añádelo a los dos y comprueba que
sigue pasando. Y `laplace ui` necesita que la interfaz esté construida
(`python scripts/build_ui.py` antes de publicar el paquete); si falta, la API arranca y
dice cómo construirla en lugar de servir un 404 mudo.

**La copia de la interfaz dentro del paquete ya no manda, pero sigue ahí.** El orden se
invirtió: gana `apps/web/out` (D-081), y al arrancar el log dice qué directorio se está
sirviendo y avisa si hay una segunda copia construida. Aun así, antes de publicar el
wheel hay que acordarse de `python scripts/build_ui.py`: en el paquete la única copia que
existe es ésa.

**El agente sin instrumentar manda.** `examples/agente_sin_instrumentar.py` es lo que
sale de leer diez líneas del README con prisa, y es el que decide si un cambio en las
reglas sirve. Un cambio que sólo se ve bien en `agente_ejemplo.py`, que está
cuidadosamente instrumentado, no está verificado.
