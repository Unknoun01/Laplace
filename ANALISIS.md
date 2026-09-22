# Análisis de Laplace

Revisión del producto entero —código, interfaz y comportamiento con tráfico real— hecha
el 22 de septiembre de 2026 sobre el commit `da82646`.

El método importa para leer lo que sigue: no es una lectura de código. Se levantó el
modo local (`laplace ui`), se le metió tráfico con `laplace demo` y con
`examples/agente_ejemplo.py`, y se recorrieron las seis pantallas en los dos modos y a
ancho de móvil. Los defectos de la sección 5 se vieron en pantalla antes de buscarlos en
el código, que es el orden que encuentra las cosas que un test no mira.

## 1. Qué es

Observabilidad **de coste** para agentes de IA. SDK de Python → spans OTel con las
convenciones GenAI → ingesta OTLP → coste calculado por tramos → reglas de derroche →
una interfaz que lo cuenta en dinero. El ciclo es `observar → evaluar → optimizar`, con
las dos primeras partes cerradas y el diagnóstico automático con modelo (Fase 3)
pendiente.

Lo que lo separa de un visor de trazas no es la traza: es que **se niega a afirmar lo
que no sabe**. Sin datos suficientes no hay porcentaje; con los márgenes solapados no
hay ganador; por debajo de un día no se proyecta el mes; un modelo sin tarifa no cuesta
cero, cuesta «no lo sabemos». Esa disciplina está escrita en 112 decisiones
([`DECISIONS.md`](DECISIONS.md)) y sostenida por pruebas. Es el activo principal del
proyecto, por encima de cualquier funcionalidad concreta.

## 2. Arquitectura y superficie

| Capa | Stack | Tamaño |
|------|-------|--------|
| SDK | Python, OTel GenAI, auto-instrumenta `openai` y `anthropic` | 16 módulos |
| Backend | FastAPI, tres almacenes (SQLite / ClickHouse + Postgres) | 17 módulos, ~8.000 líneas |
| Web | Next.js 14 + React 18, **sin dependencias de interfaz** | 5 páginas, 6 componentes, 2.005 líneas de CSS propio |
| Pruebas | 23 ficheros, cuatro niveles de alcance | ~5.000 líneas |

**32 endpoints**: trazas, overview, hallazgos, panel, alertas, precios, anotaciones,
juez, conjuntos, tiradas, comparación y prompts. `ruff` y `tsc --noEmit` pasan limpios.

Dos caminos con paridad probada: modo local de un proceso contra SQLite, y nube con
Docker contra ClickHouse y Postgres. Es el mismo código de producto; lo único que cambia
es dónde están las filas (D-015).

## 3. Inventario funcional

**SDK.** `init()` y `@observe`; auto-instrumentación de OpenAI y Anthropic incluido
streaming; identidad de paso por camino de llamada y huella de instrucciones;
`get_prompt()` con caché de un minuto, copia vencida y `fallback=` del código;
`run_dataset()`; `laplace ui` y `laplace demo`.

**Motor.** Cuatro reglas —repetición exacta, bucle sin avance, modelo caro y contexto
fijo—, cada una con una versión en tokens y tiempo para cuando no hay tarifa. Coste por
tramos (entrada nueva, lectura de caché, escritura de caché, salida) más los metros que
pida la petición. Cobertura de cuatro señales. Alertas a Slack con periodo de calma.

**Interfaz.** Seis pantallas con un interruptor global **Diagnóstico / Avanzado** que se
recuerda y se aplica antes del primer pintado. El proyecto y el rango viven en la URL,
así que cualquier vista es enlazable tal y como se está viendo. Árbol de traza navegable
con teclado, modo en vivo por polling de cinco segundos con pausa, y estados vacíos
escritos uno a uno.

## 4. Lo que está bien, y conviene no tocar

- **Las guardas.** Verificado en pantalla: el panel con un minuto de datos dice
  «todavía no hay con qué comparar» en lugar de inventar un 193.100 %.
- **La cobertura delante del dinero.** Resuelve el peor fallo posible de este producto,
  que es el silencio que parece éxito.
- **El modo dual.** En Avanzado aparecen los identificadores completos, la columna de
  modelos, el filtro por modelo y por coste mínimo, los atributos de cada span y la
  consulta que disparó cada hallazgo. En Diagnóstico desaparece todo eso sin dejar huecos.
- **Los estados vacíos.** `NoProject` da el fragmento de código con el origen del propio
  servidor ya escrito dentro.
- **Cero dependencias de interfaz**, con `focus-visible` en todos los interactivos y
  `prefers-reduced-motion` respetado.

## 5. Defectos encontrados

Ninguno de éstos estaba en [`STATUS.md`](STATUS.md). Los dos primeros son el mismo fallo
con dos caras, y eso importa más que cualquiera de los dos por separado.

### P0 — La ficha de todo hallazgo de tipo `bucle` devuelve 404

`detail()` en `insights.py` tiene rama para `repeticion`, `modelo_caro` y
`contexto_fijo`, y no la tiene para `bucle`: cae al `return None` final.

En pantalla, con el proyecto de demo, la tarjeta de 0,60 $ —la segunda que más dinero
devuelve— lleva a una página que dice «ese problema ya no aparece. O lo has arreglado, o
ha dejado de darse. **Enhorabuena** en cualquiera de los dos casos». Los cuatro
hallazgos de bucle del proyecto hacen lo mismo.

La regla de bucles entró en D-109 como «el diferenciador del producto desde el
principio», se añadió a `detect()` y nunca a `detail()`.

### P0 — El producto afirma que un modelo no tiene tarifa cuando sí la tiene

En `_expensive_model_finding()`, dos situaciones distintas entran por la misma puerta:

```python
if price is None or not price.alternative:
    return _modelo_caro_sin_tarifa(...)
```

*No está en la tabla* y *está pero ya es el más barato* no son lo mismo. En el segundo
caso la pantalla escribe «gpt-5.6-luna no está en la tabla de precios» para un modelo
que está en `model_prices.json` y cuyo coste se pinta en las otras diez pantallas. En el
mismo inicio, `unknown_cost_spans` vale 0 y `models_without_price` viene vacío.

Es exactamente el fallo que D-107 puso un guardia para impedir, con la cara contraria:
el guardia comprueba que **haya** un motivo, no que el motivo **sea cierto**.

### Lo que une a los dos, que es lo que hay que arreglar

Los dos son una regla que entró por la puerta de `detect()` y nadie comprobó que
saliera por la de `detail()`; y un guardia que mira la forma de un mensaje y no su
verdad. **Ningún test exige que todo hallazgo producido por `detect()` tenga ficha, ni
que un «no lo sabemos» sea verdad.** Las dos redes que faltan valen más que los dos
arreglos.

### P1 — D-106 cambió en silencio el significado de la pestaña Prompts

`observed_steps()` agrupa las variantes por `step_key`, y su docstring dice, citando
D-060, que «dos claves bajo la misma etiqueta son dos juegos de instrucciones del mismo
paso». Eso dejó de ser cierto cuando D-106 metió el **camino de llamada** dentro de
`step_key`.

En pantalla: el paso `redactar` sale con «3 juegos de instrucciones» y las tres filas
enseñan el mismo texto carácter por carácter. Lo que cambia es quién llama, no el
prompt. La pantalla afirma un cambio de prompt que no ocurrió.

Colateral: la guarda de D-093 —«más de ocho variantes es una plantilla con datos
dentro»— ahora se puede disparar por tener nueve llamantes, y es el único sitio del
producto donde esa patología se nombra.

### P1 — Dos hallazgos indistinguibles en la lista del inicio

Mismo origen. El título de un bucle lleva el nombre de la función, y con identidad por
camino de llamada el inicio enseña dos tarjetas con el mismo texto y distinta cifra:

```
«consultar_manual» da hasta 6 vueltas sin avanzar     $0.6045 ya gastado
«consultar_manual» da hasta 6 vueltas sin avanzar     2.36 s de espera evitable
```

De las «10 cosas que arreglar», cuatro se leen como duplicados. El producto está pagando
el coste de D-106 sin cobrar su beneficio.

### P2 — Dos convenciones numéricas en la misma línea

`format.ts` usa `Intl.NumberFormat("es-ES")` en `number()` (punto de millares), coma
decimal en `decimal()` y `toFixed()` crudo en `money()` (punto decimal). En la cabecera
de una traza conviven:

```
120.255 / 108        $0.007181        12,8 pasos por ejecución
```

Tres lecturas del mismo carácter en una pantalla, en un producto cuyo argumento entero
es que una cifra se enseña con lo que haga falta para leerla bien.

### P2 — Detalle, todo confirmado en pantalla

- **El modelo, dos veces en cada fila del árbol.** `TraceTree` pinta `span.name` y
  después `span.llm.request_model`, y el nombre del span ya es `chat gpt-5.6-luna` por
  convención OTel.
- **Tokens llamados «palabras».** El título dice «las mismas 20.040 palabras» y el
  cuerpo, dos líneas más abajo, «20040 tokens». Ni la unidad ni el separador coinciden.
- **Desbordamiento horizontal en móvil.** A 375 px el documento mide 394 y la columna
  «¿Bien?» queda cortada. Hay dos media queries para 2.005 líneas de CSS.
- **El envoltorio de un solo hijo.** Cada paso de herramienta tiene un único hijo con
  los mismos tokens y el mismo coste: seis niveles de árbol sin un dato nuevo.
- **`agente_ejemplo.py` dice «listo» después de fallar.** Apunta a `:8000`; contra
  `laplace ui` (:8100) el exportador se rinde y el script imprime igualmente «listo».
- **`POST /api/pricing/reload`** no lleva `project_id`, así que cualquier clave válida
  recarga la tabla de precios de toda la instalación. Es la primera ruta que se escapa
  del modelo «la clave ata el proyecto».

### Presentación: el héroe con el 100 % evitable

Con el proyecto de demo, el inicio enseña `$0.7514 → $0` y la barra de reparto dice «$0
de trabajo real». El aviso de `savings_needs_caution` salta bien, así que la guarda
funciona; lo que no funciona es la forma. Una barra de reparto con un lado a cero no es
una barra, y «puedes dejar de pagar absolutamente todo» es la clase de cifra que hace
que alguien cierre la pestaña. Por encima de cierto ratio necesita un tratamiento propio.

## 6. Deuda estructural

Ya estaba en `STATUS.md`; aquí sólo se confirma.

1. **Autenticación de claves, no de cuentas.** Sin login, roles, rotación, caducidad ni
   registro de accesos. Es el techo comercial.
2. **Nunca ha corrido con volumen.** `FINAL` en toda consulta de ClickHouse, sin
   rollups, sin ventana por defecto en la lista de trazas (D-008b), payloads enteros sin
   TTL.
3. **Sin borrado ni retención.** `delete_project` existe en los dos almacenes y **no
   está expuesto en ningún endpoint**.
4. **Sólo Python.** `packages/sdk-js` es un README.
5. **El wheel publicable no se ha construido nunca.**
6. **Precios que caducan cada 30 días**, y ninguna cifra en dólares validada contra una
   factura real: las cuatro pruebas que lo harían esperan una clave de proveedor.

## 7. Qué hacer, por orden

**Credibilidad, primero.** Para cada uno de los dos P0, la red antes que el arreglo:
el test que lo habría cazado, comprobado en rojo, y después la corrección.

1. Rama `bucle` en `detail()`, y un test que recorra todo lo que produce `detect()` y
   exija ficha para cada hallazgo.
2. Separar `price is None` de `not price.alternative`, y extender el guardia de D-107 a
   «el motivo es verdad», no sólo «hay motivo».
3. Agrupar las variantes de Prompts por huella de instrucciones, no por `step_key`.
4. Meter el llamante en el título de los hallazgos de bucle y repetición.
5. Tratamiento propio del héroe cuando el evitable se acerca al gasto entero.

**Pulido.** Un solo formateador numérico con locale explícito y un test que prohíba
`toFixed` suelto en pantalla; tokens son tokens en todas partes; colapsar el envoltorio
de un solo hijo y quitar el modelo duplicado; tabla con scroll propio en móvil; que el
agente de ejemplo falle ruidosamente si el exportador no entregó.

**Antes de enseñárselo a un cliente.** Retención y borrado expuestos, con política
escrita para los payloads en crudo. Una prueba de volumen de verdad: sin ella, «algo se
pondrá lento y no sé qué» sigue describiendo el producto en producción. Y gastar los
céntimos de las cuatro pruebas vivas, que es lo único que convierte «tus números son los
del proveedor» de promesa en hecho.

**Cuando haya usuarios.** Identidades, SDK de TypeScript y la Fase 3.

## 8. Lectura de conjunto

El producto está mejor pensado de lo que está terminado, y esa es la mejor forma de
estarlo. El rigor de no afirmar lo que no se sabe es genuinamente raro y es lo que hay
que proteger.

Los dos P0 no son dos descuidos: son la señal de que las reglas del proyecto se cumplen
hoy porque alguien se acuerda, y no porque el camino contrario no exista. Ése es
justamente el criterio que D-083 y D-097 aplicaron a la mezcla de veredictos y a la
autenticación, y que todavía no se ha aplicado al catálogo de hallazgos ni a los avisos
de «no lo sabemos». Arreglar los dos fallos es una tarde; poner las dos redes es lo que
evita el tercero.
