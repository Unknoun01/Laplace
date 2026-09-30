# Estado de Laplace

Última actualización: 30 de septiembre de 2026.

## Dónde está el producto

Laplace observa agentes de IA y responde a dos preguntas: **cuánto cuestan** y **si
responden bien**. Los dos ciclos están cerrados y funcionando de punta a punta por los
dos caminos —la instalación de nube con Docker y el modo local de un solo proceso—. Un
agente instrumentado con una línea de Python emite trazas por OTLP, la ingesta les
calcula el coste por tramos de tokens, cinco reglas deterministas encuentran el derroche,
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
Anthropic, no contra dobles escritos por nosotros. Eso último ha llegado ahora hasta donde
puede llegar sin pagar: un modelo local sirviendo la API de OpenAI deja al SDK publicado
hablando por HTTP con un modelo de verdad. **Lo que sigue esperando una clave es lo único
que importa de verdad: que nuestras cifras sean las que factura el proveedor.**

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
- **Y ahora también contra un modelo que corre en tu portátil**, sin falsear ni el
  transporte: Ollama sirve la API de OpenAI, el cliente real recibe ese `base_url` y una
  clave ficticia, y se recorre el camino entero con un servidor al otro lado. Cuesta cero
  y se salta solo si no hay servidor (D-102). Pasadas el 17 de septiembre contra Ollama
  0.34.1 con `qwen2.5:0.5b`: **16 de 16, ninguna saltada**, en menos de un minuto. **No valida el modelo de coste**: un modelo
  local no factura, su caché sólo reporta lecturas y con reglas propias, y cuenta
  tokens con otro tokenizador; lo que no puede dar —escrituras en caché, la forma de
  Anthropic— se simula en un fichero aparte y marcado (D-103, D-104, D-105).
- **Un agente de ejemplo deliberadamente mediocre**, corriendo de verdad contra dos
  modelos locales, con un agente sano al lado que hace el mismo trabajo bien escrito.
  Existe para ver qué detecta Laplace con tráfico real y, sobre todo, **qué no**: de las
  cinco patologías que lleva dentro, al principio se veían dos. Las otras tres, más seis
  fallos del propio producto, salieron de mirar la pantalla con esos datos delante
  (D-105 a D-110).
- **La identidad de un paso es el camino de llamada**, no el nombre de la función. Con
  el nombre, dos agentes con una función homónima mezclaban sus poblaciones y un
  llamante sano tapaba a uno roto: la media escondiendo el caso que importa, por segunda
  vez (D-106).
- **«No lo sabemos» ya no puede llegar a pantalla como un cero.** Un guardia recorre los
  modelos de la API y exige que toda cifra en dólares venga con algo que diga si se
  puede afirmar; al escribirlo encontró tres sitios más de los tres que ya se conocían
  (D-107).
- **Las tres reglas de dinero funcionan sin tarifa**, expresadas en tokens y tiempo, que
  se miden siempre. Antes, con modelos locales, dos de las tres no podían disparar nunca
  y un agente que movía ocho veces más tokens no producía ni un hallazgo (D-108).
- **Y hay una regla de bucles de verdad**, que era el diferenciador del producto desde el
  principio y no existía: lo único que había era repetición exacta, y un bucle con
  contador de intentos es invisible para eso (D-109).
- **Ninguna consulta escoge «una fila cualquiera».** El patrón `any()` / `argMax` sin
  desempate / `ORDER BY` sin desempate apareció por cuarta vez y se cerró entero en los
  dos almacenes, con pruebas de paridad sobre tráfico **empatado a propósito** y dos
  guardias que leen el código para que no vuelva a entrar (D-099).
- **Escribir en caché se cobra, y ahora también en OpenAI.** El motor ya lo aplicaba y
  Anthropic estaba completo; faltaba leer `prompt_tokens_details.cache_write_tokens`, así
  que esos tokens se cobraban a tarifa de entrada y **nuestro coste de OpenAI salía por
  debajo del real** (D-101).
- **364 pruebas, 360 pasando y 4 saltadas** con ClickHouse y Postgres levantados: las 4
  son las que necesitan una clave de proveedor. El camino de la nube se ejecuta, que es
  lo que faltaba: una tanda que toca SQL de nube y se entrega con esas pruebas saltadas
  está sin terminar (D-112). Con los almacenes en pie la suite tarda dos minutos y medio;
  sin ellos tardaba trece, y todo ese tiempo eran esperas de conexión. Las de prompts cubren las guardas
  compartidas con Evaluaciones y que un pico no se atribuya a un despliegue por la hora;
  las de cobertura, que un paso partido no pase por sano; las
  de autenticación son casi todas **intentos de hacer lo que no se debe poder**; y las de
  paridad, que los dos almacenes elijan lo mismo cuando hay empate. **Cada tanda nueva se
  ha verificado rompiendo el código a propósito**: veintidós mutaciones en total —abrir
  una ruta, dejar de mirar el cuerpo de una escritura, no acotar la traza por id, volver a
  `any()`, quitar un desempate, dejar de leer los tokens de escritura de caché, marcar
  todo como estimado, marcar nada, devolver coste cero para un modelo sin tarifa, tragarse
  un error del servidor— y todas ponen la suite en rojo. Las 4 que siguen saltándose son
  las que llaman a la API real de los proveedores y necesitan una clave.
- **El catálogo de hallazgos tiene red, y los avisos de «no lo sabemos» también.** Un
  repaso del producto con tráfico real delante encontró dos fallos que eran el mismo con
  dos caras: una regla que entró por `detect()` y nunca salió por `detail()` —los cuatro
  bucles del proyecto de demo llevaban a un 404 que decía «enhorabuena»— y un guardia que
  comprobaba que hubiera un motivo pero no que el motivo fuera cierto —«no está en la
  tabla de precios» de un modelo que sí estaba—. Los dos arreglos son una tarde; lo que
  se ha puesto son las dos redes que los habrían cazado (D-113, D-114).

## Qué queda

El orden y el detalle están en [`docs/HOJA_DE_RUTA.md`](docs/HOJA_DE_RUTA.md), que es
el documento que se mantiene al día. En corto: las fases 3, 4 y 5 están cerradas. La
Fase 5 dejó el producto en cinco idiomas (D-147 a D-149), con menos texto, gráficos y
grafo del agente (D-150 a D-153), el oscuro como tema por defecto (D-155) y la interfaz
alrededor del ciclo detectar → probar → arreglar → verificar, con Prompts como fuente de
hallazgos (D-156, D-157). La Fase 6, margen por cliente, está cerrada (D-161, D-162); Stripe está hecho contra
una Stripe falsa y falta probarlo con una clave de pruebas. El paquete fino
de TypeScript espera el nombre en npm, y el diagnóstico automático con modelo sigue con
su hueco reservado en el contrato, el esquema y la API.

La deuda de la hoja de ruta quedó casi cerrada el 30 de septiembre:

- ocho detalles con su prueba (D-171);
- la guardia de las reglas nuevas, para que una sexta no titule dos hallazgos igual ni
  reclame dinero dos veces (D-172);
- retención por proyecto y borrado de los datos de una persona o un cliente (D-173);
- la hoja de estilos partida, un índice de decisiones y documentación de usuario en
  `docs/usuario/` (D-174);
- las pantallas con sesión recorridas en un navegador (D-175) y el contraste medido sobre
  cada pantalla, no sólo sobre los tokens (D-176).

Y la escala llegó al objetivo del día con los preagregados por minuto y la clave por
hora (D-177). Quedan Safari (en el contenedor sólo hay Chromium) y lo que es del usuario:
el castellano de la línea de órdenes y las carpetas de Docker en Windows.

## Con un framework de verdad: LangGraph, y un agente en Node

Probado el 26 de septiembre con `langgraph` 1.2.12, `langchain-openai` 1.6.6 y
`openinference-instrumentation-langchain` 0.1.76: un agente `create_react_agent` con
una herramienta, dos preguntas, contra un servidor que responde como OpenAI (pide la
herramienta y después contesta), exportando a `laplace ui`. Y en Node, `openai` 7.23 con
OpenInference-js 4.2.7 y con OpenLLMetry-js 0.27 (D-139).

* **Sólo con OpenInference se ve casi todo.** El árbol entero (LangGraph → agent →
  call_model → ChatOpenAI, y tools → franquicia), cada llamada con modelo, tokens,
  lecturas de caché y coste exacto, y la herramienta como herramienta. Lo que falta es
  el sitio de llamada: el paso se reconoce por sus instrucciones y no por desde dónde se
  llama, así que dos nodos con el mismo prompt se juntan. OpenInference manda el nodo de
  LangGraph en `metadata.langgraph_node`, y ese es el candidato obvio para rellenarlo.
* **Sólo con `laplace.init()` se ve la mitad.** Cada llamada llega como una traza suelta
  de un span, con su coste bien, pero sin árbol y sin la herramienta: sin `@observe` el
  SDK no sabe qué hay alrededor de la llamada. Es lo esperado, pero es lo primero que va
  a ver quien lo pruebe con LangGraph.
* **Con los dos a la vez, cada llamada se cuenta dos veces.** Ocho llamadas para cuatro,
  en trazas distintas: el instrumentador de LangChain no deja su span como activo, así
  que el nuestro no cuelga de él, y no manda el id de respuesta, así que tampoco se
  pueden emparejar en la ingesta. **Está sin arreglar** y es una decisión: avisar, dejar
  de parchear OpenAI cuando hay otro instrumentador de LLM activo (y perder las llamadas
  directas que no pasen por él), o emparejar por contenido y tiempo.
* **En Node**, OpenInference-js se ve igual de bien que en Python. OpenLLMetry-js no
  manda los tokens leídos de caché de OpenAI, así que ese coste sale por encima de la
  factura sin que Laplace pueda saberlo. Y la prueba destapó dos fallos de la ingesta
  que ya están arreglados: los ids de OTLP/JSON y las convenciones GenAI nuevas (D-139).
* **Sin hallazgos, y es lo correcto:** con cuatro llamadas no hay muestra, y la cobertura
  lo dice («hacen falta 10»).

## El repaso honesto

Lo que aguantaría un usuario real mañana y lo que no, actualizado después de cerrar
cobertura, autenticación y proveedores, y después de **mirar el producto entero en
pantalla con tráfico real delante** en lugar de leer el código. Ese repaso está en
[`docs/auditoria-producto.md`](docs/auditoria-producto.md) y encontró seis cosas que ninguna prueba veía, dos de ellas
graves, porque todas se manifestaban en la pantalla y ninguna en una aserción. **La
lección es del método, no de los fallos:** una suite de 333 pruebas en verde no dice que
el producto se lea bien, y este producto es sobre todo lo que el usuario lee.

**Aguanta.** El modo local entero: `pip install`, `laplace ui`, una línea, y ves tus
trazas con su coste. El modelo de coste por tramos, con sus suelos y sus «no lo sabemos».
Las tres reglas de derroche, con cinco cicatrices de doble conteo y su test cada una. El
panel. Evaluaciones. Prompts. Y ahora la cobertura, que es lo que hace que el silencio de
todo lo anterior se pueda interpretar. Las integraciones de OpenAI y Anthropic ya no son
un acto de fe: corren contra los clientes reales en cada `pytest`, y con un modelo local
levantado corren además contra un servidor de verdad, por HTTP, sin falsear el cuerpo de
la respuesta.

### Lo que sólo se ve mirando la pantalla

Seis defectos que la suite no veía, todos encontrados abriendo el producto con datos
reales. Los cinco arreglados están en D-113 a D-118; lo que importa aquí es la forma que
tenían, porque la próxima se parecerá:

* **Dos eran el mismo fallo con dos caras.** Una regla que entró por `detect()` y nunca
  salió por `detail()`, y un guardia que comprueba que haya un motivo pero no que el
  motivo sea cierto. Los dos arreglos son una tarde; lo que faltaba eran las dos redes.
* **Dos venían de D-106**, que metió el camino de llamada en `step_key` sin repasar quién
  consumía esa clave con la definición anterior. El barrido dio tres sitios con la
  suposición vieja y uno que ya estaba bien.
* **Uno estaba tapado por un tope.** El `min(suma, gasto)` convertía un solape de reglas
  en un «100 % evitable» que la pantalla enseñaba como buena noticia.
* **Y uno era un test que fallaba por el reloj**, no por el código: 48 minutos de cada
  seis horas.

Los ocho puntos de aquel repaso están cerrados menos dos, y el séptimo resultó ser
otra cosa: al ir a acotar `POST /api/pricing/reload` —la única ruta sin `project_id`—
apareció que había **seis** rutas por id opaco sin acotar, y que con la clave de un
proyecto se podían borrar y leer los prompts de otro (D-121). El punto ciego que D-097
dejó anotado existía, y era más grande que la nota.

Los dos que quedaban sin tocar a propósito —el envoltorio de un solo hijo en el árbol y
la media sin ponderar de `_modelo_mas_rapido()`— se decidieron en D-160: el envoltorio se
queda con «=» en sus cifras, y la media pondera por llamadas.

### Lo que sigue sin detectarse, y por qué

Del agente de ejemplo, con los seis arreglos puestos, Laplace ve cuatro de las cinco
patologías con tráfico real: las dos repeticiones, el bucle —por los dos caminos, modelo
y herramienta— y el paso partido por la fecha.

Las otras dos merecen una frase cada una, porque **callarse es la respuesta correcta** y
conviene que quede escrito por qué:

* **El modelo caro en un paso trivial.** Sin tarifa lo único que se puede afirmar es el
  tiempo, y sobre esta máquina el modelo grande tarda un 26 % más que el pequeño para
  responder dos tokens: por debajo del umbral de 1,8x. Con precios reales la regla de
  siempre funciona igual; sin ellos, aquí no había nada que decir.
* **El contexto fijo reenviado.** Ollama cachea el prefijo por su cuenta y sirve el
  91 % de esos tokens desde caché, así que lo que se reenvía de verdad es poco y la
  regla se calla. Contra OpenAI la caché también es automática por encima de 1.024
  tokens, pero ahí las lecturas **sí se cobran** —entre un 10 % y un 50 % de la entrada
  según el proveedor—, y eso es dinero que esta regla todavía no mira. Es el siguiente
  paso de la regla 3, y está sin hacer.

### Lo que los tests contra modelo local NO verifican

Está aquí y no en una nota al pie porque es lo que más fácil es malinterpretar. Si alguien
lee «tests contra proveedor real en verde» y entiende «el modelo de coste está validado»,
**ha entendido mal**, y la culpa sería de este documento.

Lo que esas pruebas demuestran es que **el camino funciona**: el parche llega a la clase
que el cliente usa, el cuerpo que devuelve un servidor ajeno se parsea, el SSE se lee
troceado por la red, el span sale y la ingesta lo traduce. Todo eso estaba antes probado
con el transporte falseado y ahora está probado con un servidor al otro lado.

Lo que **no** demuestran, punto por punto:

1. **Que los tokens que guardamos sean los que alguien cobró.** Un modelo local no
   factura. No existe factura contra la que cuadrar nada. Lo único que se comprueba es
   que el span dice lo mismo que *reportó el servidor*. La promesa del producto —«tus
   números son los del proveedor»— sigue apoyada **sólo** en las 4 pruebas vivas que
   esperan una clave, y hasta que se pongan, el modelo de coste está validado contra la
   aritmética que escribimos nosotros y contra precios publicados, no contra una factura.
2. **Sólo la mitad del tramo de caché.** Ollama reutiliza el prefijo y lo reporta como
   `cached_tokens`, con la forma de OpenAI: la **lectura** de caché se prueba de verdad.
   Pero cachea cualquier prefijo repetido, no con las reglas de OpenAI (desde 1.024
   tokens, en bloques), y no reporta nunca escrituras. `cache_write_tokens` y el reparto
   5 min / 1 h —lo que ya se equivocó una vez (D-101)— siguen ejercitándose con
   contadores **inventados por nosotros**, en `test_modelo_local_simulado.py`. Esto decía
   antes «nada del tramo de caché», y era falso (D-105).
3. **Ninguna cifra en dólares.** El tokenizador del modelo local es el suyo, con su
   vocabulario: sus recuentos no se parecen a los de `o200k` ni a los de Anthropic. Por
   eso ninguna de esas pruebas comprueba un importe, sólo de dónde sale cada número y
   cómo queda marcado. La distancia no es pequeña: con «Di la palabra hola y nada más.»
   el servidor contó 38 tokens de entrada y nuestra estimación, 15. Buena parte de esa
   diferencia es la plantilla de chat de Qwen, que mete su propio prompt de sistema, así
   que tampoco dice cuánto se desvía la estimación contra OpenAI. Lo que sí confirma es
   que la marca de «estimado» no es decorativa.
4. **Anthropic, por este camino, nada.** Ollama habla la API de OpenAI; no hay servidor
   local que hable la de Anthropic. Escribir el traductor nosotros sería volver a probar
   contra un doble propio, así que no se ha hecho. La integración de Anthropic se queda
   con el transporte falso y con las pruebas vivas.

**No aguanta todavía, por orden de riesgo:**

1. **Identidades: hay cuentas, falta lo de empresa.** Desde D-127 hay cuentas,
   organizaciones, cuatro roles, invitaciones, registro de actividad y claves por
   proyecto con caducidad. Lo que falta para vender a empresas es entrar con Google o
   GitHub (SSO/SAML), aprovisionar por SCIM y verificar el correo.

   Y hay que decir una cosa más, porque esta sección la daba por cerrada: **la
   separación entre clientes tenía seis agujeros y estuvieron ahí desde que existe la
   pestaña de Prompts**. Todo lo que va por id opaco —borrar un prompt, leer su texto,
   borrar una anotación o un conjunto— no lo mira el middleware, y no se acotaba. Está
   arreglado y con guardia estructural (D-121), pero lo que aprende esto no es que ya
   esté: es que el modelo de permisos de este producto se ha comprobado **dos veces con
   la misma prueba** —«¿puede una clave leer las trazas de otro proyecto?»— y las dos
   veces se dio por bueno el resto sin mirarlo.
2. **Volumen: el día, en el objetivo; la semana, todavía no.** Con la tabla ordenada por
   hora y los preagregados por minuto (D-177), el Diagnóstico de un día de un proyecto con
   diez millones de spans al día tarda 1,1 s en un contenedor de 4 núcleos (6,5 s con la
   clave por día de D-168, 15–19 s con la de antes). El de 7 días, unos 6 s (antes, casi
   un minuto): ahí pesan los recuentos exactos de ejecuciones distintas. Las
   instalaciones que ya existen se migran a mano (`migrar_orden`). La API de la lista de trazas sigue
   escaneando sin ventana si no se le pasa una (D-008b); la interfaz siempre la pasa.
3. **TypeScript, sin SDK propio.** Un agente en Node se ve con OpenInference-js u
   OpenLLMetry-js (con OpenAI o Anthropic), el AI SDK de Vercel 7 o LangChain.js
   apuntados a Laplace, con una guía probada (`docs/typescript.md`) y un banco que se
   puede repetir (`scripts/integraciones_js`, D-165), pero sin gestión de prompts ni el
   resto de ayudas del SDK de Python. Lo de D-165 se probó contra un proveedor falso,
   no contra la API real.
4. **Los paquetes no se han publicado.** `paquete.yml` construye los dos wheels con la
   interfaz dentro (D-134), pero publicarlos espera a que se reserven los nombres en
   PyPI.
5. **La retención es de toda la instalación, no por proyecto.** Desde D-123 se borra un
   proyecto desde Ajustes y `LAPLACE_RETENTION_DAYS` caduca las trazas, pero el mismo
   plazo vale para todos: D-009 pedía retención por proyecto y sigue sin hacerse. Borrar
   los datos de un usuario final concreto (por `user_id`) tampoco existe.
6. **Los precios caducan cada 30 días** y hay que reverificarlos. Tres tests lo avisan.
   El `cache_write_tokens` de OpenAI que estaba anotado como incompleto ya está cerrado
   (D-101), pero el aviso de fondo sigue: un campo nuevo en la respuesta de un proveedor
   no falla, sólo se lee como `None`, y el coste sale por debajo sin que nadie se entere.
   La comprobación de forma de `test_modelo_local_simulado.py` tapa media rendija —que un
   campo nuestro esté mal escrito— pero no la otra: un campo que el proveedor añade y el
   SDK todavía no declara no se ve desde aquí.

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

**«Verde» no quiere decir lo mismo en cada tanda de pruebas, y hay que decir cuál.**
Cuatro niveles con cuatro alcances distintos: sin red, contra los almacenes, contra los
SDK con transporte falso, y contra un modelo local por HTTP. Ninguno de los cuatro puede
decir que nuestras cifras son las del proveedor: eso sólo lo dicen las cuatro pruebas
vivas, que no corren con un `pytest` a secas porque necesitan clave. La tentación
concreta a la que no ceder es enseñar «tests contra proveedor real: 316 en verde» sin esa
frase detrás; el aviso está escrito arriba y en la cabecera de los dos ficheros de
pruebas, y si se quita de ahí, se queda sin decir en ningún sitio. Y una regla para la
próxima simulación: lo simulado va en un fichero propio con «simulado» en el nombre, no
como un caso más entre los reales.

**El doble conteo es el fallo recurrente de este proyecto.** Ha aparecido por **cinco**
caminos distintos: dos reglas sobre los mismos tokens, dos reglas sobre el mismo paso, el
cruce del descuento sin pareja al cambiar la clave de agrupación, el descuento de tokens
sin descontar llamadas, y la regla de bucles que entró sin enchufarse al descuento
(D-117). Cada uno dejó su caso en `test_insights.py`. Reglas al tocar el motor: los casos
se añaden, nunca se sustituyen; las cifras se comprueban exactas y no con un tope contra
el gasto total, que deja pasar el error mientras quepa dentro; y hay que comprobar que
los tests muerden rompiendo el motor a propósito. **El test de regresión del solape no se
toca.**

Y una lectura del quinto que vale para el sexto: **`min(suma, gasto)` en `overview()` es
un cinturón, no un cálculo.** Cuando llega a morder, el héroe enseña «100 % evitable» y
eso se lee como una buena noticia en lugar de como lo que es: dos reglas reclamando el
mismo dinero. Hay una prueba que exige que no muerda. Si alguien la relaja porque «total,
el tope ya lo acota», habrá devuelto el fallo a su escondite.

**Una regla nueva entra por dos puertas, no por una.** `detect()` la encuentra y
`detail()` la explica, y la de bucles se entregó cuatro tandas sólo con la primera: su
hallazgo más caro llevaba a un 404 que el usuario leía como «enhorabuena» (D-113). Se
dejó además sin desambiguar el título (D-115) y sin enchufar al descuento (D-117): tres
medias entregas de la misma regla. `DETAILED_KINDS` y `test_catalogo_hallazgos` cierran
la primera; las otras dos no tienen guardia estructural todavía, así que al añadir una
regla hay que recorrer a mano quién más tenía que enterarse.

**Un «no lo sabemos» tiene que ser verdad, no sólo existir.** El guardia de D-107
comprueba que una cifra en dólares venga con su compañera; no comprueba que la compañera
diga algo cierto, y por ahí se afirmó que un modelo no tenía tarifa cuando sí la tenía
(D-114). Toda forma nueva de decir «no hay tarifa» va en
`dinero.AFIRMACIONES_DE_SIN_TARIFA` o el barrido no la mira.

**`step_key` ya no significa lo que su nombre sugiere.** Desde D-106 lleva dentro el
camino de llamada, así que dos claves bajo la misma etiqueta pueden ser el mismo prompt
llamado desde dos sitios. Dos pantallas se quedaron leyéndolo con la definición vieja y
afirmaron cambios de prompt que no ocurrieron y duplicados que no lo eran (D-115). Quien
vaya a agrupar por `step_key` tiene que decidir primero si lo que busca es un paso o un
prompt, y `pasos.py` es el único sitio que sabe escribir el nombre de un paso: si aparece
un segundo, volvemos a tener dos verdades.

**Una prueba que coloca tráfico en un instante relativo tiene que anclarlo al tramo.** El
test de paridad del panel fallaba 48 minutos de cada seis horas porque su pico cruzaba
una frontera de tramo según la hora a la que se lanzara (D-116). El arreglo ya existía en
otra fixture y la copia inline se lo perdió. Un rojo que depende del reloj enseña a no
mirar el rojo.

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

**El determinismo de las consultas se rompe en silencio y en la nube.** `any()`,
`argMax` sin desempate y `ORDER BY` sin desempate no rompen ninguna cifra: cambian qué
fila se elige, y eso sólo se nota comparando dos pantallas. Ya apareció cuatro veces. Hay
dos pruebas que leen el SQL y lo prohíben, y las de paridad siembran **empates a
propósito** porque sobre datos normales pasarían por casualidad. Si alguien añade una
consulta nueva a la nube, la regla es: `max()`/`min()` donde SQLite use `MAX()`/`MIN()`,
tupla en la clave de todo `argMax`/`argMin`, y desempate explícito en todo `ORDER BY`.

**Los nombres de modelo caducan igual que los precios, y en más sitios.** Ya se arregló
una vez en la demo y habían sobrevivido en los tests, en el `README` y en la pestaña de
Prompts. Al barrerlos hay que contar con que **mueven cifras**: las tarifas van escritas a
mano en los valores esperados, así que cambiar un fixture de modelo obliga a recalcular a
mano y a comprobar que los tests de doble conteo siguen mordiendo. Lo que no se toca son
los modelos viejos de la tabla de precios ni las pruebas que comprueban que un nombre
antiguo se resuelve: eso es la funcionalidad, no un resto.

**La autenticación se rompe también por el id opaco, no sólo por la ruta nueva.** Lo de
abajo sigue valiendo entero, pero le faltaba una mitad: el middleware acota por el
`project_id` que venga en la petición, así que **todo lo que se identifica por un id y no
por su proyecto se le escapa**. Borrar un prompt, leer su texto, borrar una anotación o un
conjunto iban por ahí, y con la clave de un proyecto se tocaban los de otro (D-121). La
regla, para la siguiente: una ruta que reciba un id opaco pide
`Identity.scope(None)` y lo pasa al almacén, que lo mete en el `WHERE`. No vale
comprobarlo en la ruta después de leer la fila: funciona igual y se olvida en la
siguiente. `test_ninguna_ruta_de_escritura_se_queda_sin_acotar` recorre las rutas y lo
exige; ese test tampoco se toca.

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
