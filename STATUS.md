# Estado de Laplace

Última actualización: 12 de septiembre de 2026.

## Dónde está el producto

Laplace observa agentes de IA y responde a dos preguntas: **cuánto cuestan** y **si
responden bien**. Los dos ciclos están cerrados y funcionando de punta a punta por los
dos caminos —la instalación de nube con Docker y el modo local de un solo proceso—. Un
agente instrumentado con una línea de Python emite trazas por OTLP, la ingesta les
calcula el coste por tramos de tokens, tres reglas deterministas encuentran el derroche,
la interfaz lo cuenta en dinero con el cálculo detrás, una alerta a Slack avisa cuando
algo pasa del umbral, el panel dice si el gasto sube porque hay más trabajo o porque el
trabajo se ha encarecido, la pestaña de Evaluaciones compara dos versiones del agente
enseñando acierto y coste a la vez, y la de Prompts enseña cada versión con lo que costó
y lo que acertó sobre el tráfico que la usó. **Las fases 2, 4.a, 5 y 6 quedan cerradas.**
Lo único que falta del plan es el diagnóstico automático con modelo.

Encima del plan hay tres cosas que no estaban y que hacían falta antes de enseñar esto a
nadie: **la cobertura** —cuánto del agente entendemos, dicho antes que cualquier cifra de
ahorro—, **la autenticación** —la instalación de nube ya no es «quien llegue a la URL lee
y escribe todo»— y **las integraciones probadas contra los SDK de verdad** de OpenAI y
Anthropic, no contra dobles escritos por nosotros.

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
| 5 | Evaluaciones: anotación, conjuntos de casos y comparación A vs B | ✅ |
| 6 | Prompts: versiones con diff, rollback y métricas por versión | ✅ |

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
- **Antes de desplegar hay una frase que leer.** «B acierta igual —hasta donde se puede
  saber— y cuesta un 90 % menos por caso» es lo que sale de comparar dos tiradas del
  mismo conjunto. Acierto y coste juntos, y el acierto sólo en porcentaje cuando hay
  casos suficientes (D-087).
- **El veredicto de las personas y el del juez no se tocan.** Ni en la base —una
  anotación humana no puede llevar coste de juez, porque escribe `NULL` en esas
  columnas—, ni en el cálculo, ni en la pantalla. Y si se contradicen, se dice (D-083).
- **Lo que cuesta nuestro juez se mide con la misma tabla de precios que el gasto
  ajeno**, se guarda pegado al veredicto y se enseña aparte del coste del agente (D-088).
- **Cada versión de un prompt lleva pegado lo que costó y lo que acertó.** «v8: 0,004 $
  por ejecución, 94 % — v7: 0,007 $, 93 %» sale del tráfico real que usó cada una, no de
  una estimación: el SDK sirve el prompt y deja escrito en cada traza con qué versión se
  ejecutó, y sólo si comprueba que ese texto iba de verdad en la llamada (D-090).
- **Servir prompts no puede tumbar al agente de nadie.** Caché de un minuto, copia
  vencida antes que excepción, y `fallback=` del código como último recurso. Ese tráfico
  de reserva se cuenta aparte y nunca como la versión de producción (D-091).
- **El que no adopte la gestión de prompts no se queda sin pestaña.** La huella del
  prompt de sistema ya vive en la identidad de paso, así que se puede decir qué pasos
  cambiaron de instrucciones y cuándo. Con la guarda de la huella partida puesta: por
  encima de ocho variantes se dice que eso es una plantilla, no un histórico (D-093).
- **El silencio del producto ya no se lee como una buena noticia.** Cuatro señales
  —paso que se distingue, tarifa conocida, tokens del proveedor, versión de prompt— en el
  inicio, delante del dinero cuando alguna está baja. Y un paso cuyas instrucciones
  cambian en cada ejecución se nombra por lo que es, aunque los cuatro porcentajes estén
  al 100 %: es el caso que se cuela por debajo de cualquier media (D-096).
- **Nadie lee los datos de otro.** Middleware que deniega por defecto en `/api` y
  `/v1/traces`, claves por proyecto guardadas sólo como hash, la ingesta atada al
  proyecto de su clave, y la lista de proyectos filtrada por identidad. Una ruta nueva
  nace protegida, y hay un test que recorre las rutas registradas y lo exige (D-097).
- **Las integraciones se prueban contra `openai` y `anthropic` de verdad**, con su
  parseo, sus modelos y su lector de SSE; lo único falso es el transporte HTTP. Las que
  además comprueban que nuestros números cuadran con la factura del proveedor están
  escritas y esperan una clave (D-098).
- **280 pruebas y ninguna saltada** con ClickHouse y Postgres levantados. Las de
  prompts cubren las guardas compartidas con Evaluaciones y que un pico no se atribuya a
  un despliegue por la hora; las de cobertura, que un paso partido no pase por sano; las
  de autenticación son casi todas **intentos de hacer lo que no se debe poder**, y las
  tres mutaciones que se probaron —abrir una ruta, dejar de mirar el cuerpo, no acotar la
  traza por id— ponen la suite en rojo. Las 4 que siguen saltándose son las que llaman a
  la API real de los proveedores y necesitan una clave.

## Qué queda

**Diagnóstico automático (Fase 3).** Pasarle la traza entera a un modelo para que diga la
causa probable de un fallo y sugiera el arreglo. El hueco sigue donde estaba desde la
Fase 0 —en el contrato, en el esquema de Postgres y en la API, devolviendo `null`—, así
que cuando llegue no hay migración.

El otro hueco reservado ya está cerrado: la atribución de picos tenía guardado el tipo de
causa para la versión de prompt y entró sin tocar nada más, por la misma puerta que las
demás causas —algo que aparece en las trazas del tramo y no aparecía antes— y no por la
hora del despliegue (D-092).

## El repaso honesto

Lo que aguantaría un usuario real mañana y lo que no, actualizado después de cerrar
cobertura, autenticación y proveedores.

**Aguanta.** El modo local entero: `pip install`, `laplace ui`, una línea, y ves tus
trazas con su coste. El modelo de coste por tramos, con sus suelos y sus «no lo sabemos».
Las tres reglas de derroche, con cuatro cicatrices de doble conteo y su test cada una. El
panel. Evaluaciones. Prompts. Y ahora la cobertura, que es lo que hace que el silencio de
todo lo anterior se pueda interpretar. Las integraciones de OpenAI y Anthropic ya no son
un acto de fe: corren contra los clientes reales en cada `pytest`.

**No aguanta todavía, por orden de riesgo:**

1. **La autenticación es de claves, no de cuentas.** No hay login, ni usuarios, ni
   organizaciones, ni roles, ni rotación, ni caducidad, ni registro de accesos. Una clave
   da acceso total de lectura y escritura a su proyecto, y quien la tenga es quien sea. La
   comparte un equipo entero copiándola. Para una instalación propia con un puñado de
   proyectos es suficiente y es infinitamente mejor que lo que había; para vender esto a
   dos clientes en el mismo despliegue, falta el sistema de identidades (D-010).
2. **No ha corrido con volumen.** Todas las cifras que se han visto salen de decenas o
   cientos de trazas sembradas. La lista de trazas escanea sin ventana temporal por
   defecto (D-008b), no hay rollups, cada consulta lleva `FINAL` y los payloads se guardan
   enteros sin retención ni TTL. Con una semana de un agente real, algo se pondrá lento y
   no sé qué.
3. **Sólo Python.** `packages/sdk-js` es un README. Cualquiera con un agente en
   TypeScript se queda fuera en la primera frase.
4. **El wheel publicable no se ha construido nunca.** `laplace ui` dentro del paquete
   depende de `scripts/build_ui.py` ejecutado antes de empaquetar; si se olvida, el primer
   `pip install` de un desconocido arranca la API y no sirve interfaz.
5. **No hay borrado ni retención.** `delete_project` existe y sólo lo usan las pruebas.
   Un cliente que pida que se vayan sus datos hoy se atiende a mano, y los prompts y
   respuestas en crudo se guardan sin caducidad.
6. **Los precios caducan cada 30 días** y hay que reverificarlos. Tres tests lo avisan.
   Y `PromptTokensDetails` de OpenAI ha ganado un `cache_write_tokens` que no leemos y
   para el que no hay tarifa verificada: si OpenAI ha empezado a cobrar la escritura de
   caché, nuestro coste de sus llamadas es un suelo más bajo de lo que creíamos.

**Lo más frágil sigue siendo la identidad de paso**, pero ya no es invisible: la cobertura
la mide, la nombra y dice cómo arreglarla. Eso cambia la naturaleza del riesgo —de «el
producto miente en silencio» a «el producto avisa de que no sabe»—, que era exactamente lo
que había que conseguir antes de enseñarlo.

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
volver a agrupar por nombre. **Desde la pestaña de Prompts esto por fin se ve**: cuando un
paso tiene más de ocho juegos de instrucciones, la pantalla dice que eso es una plantilla
con datos dentro y que sacándolos a variables Laplace podrá medirla. Es el único sitio del
producto donde esa patología se nombra, así que si se quita esa guarda se vuelve a quedar
invisible.

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

**El juez es un grifo abierto.** Está apagado por defecto y tiene tope por tanda, y esos
dos frenos son lo único que separa «pruebo el juez» de una factura sorpresa. Si alguien
añade una forma nueva de invocarlo —un botón de «juzgar todo», un disparo automático al
ingerir— tiene que llevar su tope y decir lo que va a costar **antes**, no después. El
coste ya se mide y se guarda; lo que no se puede perder es el momento en que se enseña.

**Un acierto sin margen es la cuarta cara del mismo error.** 468 $/mes desde una hora de
datos, 193.100 % contra un periodo vacío, y un «94 %» sacado de cuatro casos. Toda
proporción nueva que aparezca en el producto —tasa de error, cobertura, lo que sea—
necesita pasar por `rate_for()` o por una guarda equivalente antes de pintarse. Y en
cualquier comparación, la regla es que **si los márgenes se solapan no hay ganador**.

**La autenticación se rompe por la ruta nueva, no por la criptografía.** Todo lo que la
sostiene es que el middleware deniega por defecto y que la lista blanca tiene una entrada.
Si alguien añade una ruta y, para que «le funcione», la mete en `PUBLIC_PATHS` o la cuelga
fuera de `/api`, habrá abierto el producto entero sin tocar una línea de `auth.py`.
`test_ninguna_ruta_nueva_nace_abierta` recorre las rutas registradas y lo exige; ese test
no se toca. Y el otro flanco: si aparece una escritura que **no** lleve `project_id` en el
cuerpo ni en la URL, el middleware no tiene por dónde acotarla y hay que acotarla en la
ruta a mano. Hoy no existe ninguna; el día que exista, es el punto ciego.

**La cobertura deja de servir si se relajan sus umbrales.** Está calibrada para molestar:
por debajo del 90 % avisa y por debajo del 70 % se pone delante del dinero. La tentación
del día que un usuario diga «me sale en ámbar y mi agente va bien» es bajar el umbral. La
respuesta correcta es mirar por qué le sale: casi siempre es que de verdad no le estamos
midiendo la mitad de las llamadas. Un umbral bajado convierte esta sección en un adorno
verde, que es peor que no tenerla, porque entonces el silencio vuelve a parecer bueno y
encima con un sello de aprobación.

**Servir prompts nos pone dentro del camino caliente de un agente ajeno.** Es la
frontera de D-086 cruzada en la otra dirección, y lo único que la hace aceptable son las
tres caídas hacia atrás del SDK: caché vigente, caché vencida y `fallback=`. Si alguien
añade una forma nueva de pedir un prompt —un helper, un decorador, una integración— tiene
que llevar las tres. Una ruta que levante una excepción cuando Laplace no responde
convierte nuestra caída en la incidencia de producción de otro, y eso se descubre el peor
día posible.

**La atribución de una versión se rompe en silencio.** Si alguien «arregla» el
comprobante de texto por fiarse del último `get_prompt()` —porque un usuario dirá que su
prompt no se marca—, las métricas de cada versión empezarán a incluir tráfico ajeno y
**seguirán pareciendo correctas**. La regla es que no marcar es siempre mejor que marcar
mal: un hueco se ve, una cifra contaminada no. Si un usuario de verdad se queja de que no
se marca, la respuesta es enseñarle que está reescribiendo el texto, no relajar la
comprobación.

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
