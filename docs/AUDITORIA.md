# Auditoría de Laplace — 19 de octubre de 2026

Seis auditores independientes han revisado el proyecto entero en paralelo, sin tocar
código. Cada informe está íntegro más abajo. Este resumen junta lo más grave de todos y
propone un orden.

## Notas

| Apartado | Nota | En una línea |
|---|---|---|
| 1. Mercado y negocio | **4** | Buena cuña (probar el arreglo y verificar el ahorro), pero hoy no se puede conseguir, nada se ha pasado contra una API real, y Arize ya detecta y abre el PR. |
| 2. Seguridad | **7** | Buen diseño (se deniega por defecto, CSRF, SAML estricto); los fallos están en las costuras entre clientes y entre roles. |
| 3. Eficiencia y escala | **5** | Las lecturas, bien medidas; la ingesta nunca se ha medido con volumen, y el simhash se come el 90 % de su coste. |
| 4. Interfaz y accesibilidad | **7,5** | El Diagnóstico se entiende en 5 segundos y las cifras son honradas; falta accesibilidad estructural y sobra texto. |
| 5. Código, pruebas y operación | **6,5** | El código y las pruebas, un 8; la operación en la nube, un 4,5: sin copias de seguridad ni métricas. |
| 6. Producto y honradez de las cifras | **5** | Las guardas valen un 7, pero dos fallos confirmados fabrican cifras en el camino principal. |

## Lo más grave, de todos los informes

**Cifras que el producto no puede afirmar.** Rompen sus propias reglas y van primero:
1. **El ahorro «verificado» no verifica** (6, crítico, reproducido). `seguimiento.py`
   da un hallazgo por arreglado cuando deja de aparecer, y desaparece también si
   después sólo hay tiradas de evaluación o poco tráfico. Esas tiradas suman dinero a
   «Ya has dejado de pagar».
2. **Con `capture_content=False`, un agente sano sale con un 98 % de derroche** (6,
   crítico, reproducido). Sin contenido, `dedup_hash` es el mismo para todas las
   llamadas de un paso y la regla de repetición las cuenta como copias.
3. **El modelo caro y el contexto fijo cuentan dos veces el mismo dinero**
   (6, alto): `contexto_fijo.py:215` suma el 100 % de las lecturas de caché.
4. **«El mismo trabajo costaría 10 veces menos»** afirma que la calidad es la misma, y
   nadie lo ha medido (6, alto).

**Seguridad, antes de tener un segundo cliente en la nube:**
5. **El `installation_id` de la GitHub App no se ata a la organización**: otro cliente
   puede abrir PR en tu repositorio (2, alto; crítico con la App registrada).
6. **Hay escrituras destructivas fuera de `ADMIN_WRITES`**: con una clave de ingesta
   filtrada, o siendo un simple miembro, se vacía el histórico (2, alto).
7. **Cuatro de gravedad media** (2): SCIM da de alta a cuentas de otras organizaciones;
   el descubrimiento OIDC permite SSRF; el admin puede suplantar al propietario por SSO;
   y `laplace ui` no comprueba `Host`, lo que abre la puerta al DNS rebinding.

**Operación, antes de que haya datos que importen:**
8. **No hay copias de seguridad** ni de ClickHouse ni de Postgres; Laplace no se
   observa a sí mismo (sin métricas, sólo logs de texto); el cliente de ClickHouse no
   tiene timeouts; el compose no reinicia nada y lleva credenciales por defecto (5,
   críticos y altos).

**Escala:**
9. **El simhash de D-219 es el 90 % del coste de la ingesta**: 11–13 ms por span de 2 KB,
   y crece con el prompt (3, crítico). Además, la ingesta nunca se ha medido con
   volumen, porque la carga inserta directamente con SQL.
10. **La suite es frágil por diseño**: 13 ficheros crean y borran su propia base en un
    ClickHouse compartido, y cualquier otra actividad la bloquea (3 y 5). Por eso tardó
    10 horas en vez de 21 minutos.

**Mercado:**
11. **No hay forma de conseguir Laplace**: no está en PyPI, no hay dominio y la GitHub
    App no está registrada. Y **ninguna pieza del ciclo diferencial se ha probado
    contra una API real** (1, críticos).
12. **El diferencial «detecta y abre el PR» ya lo tiene Arize** (Dynatrace, 915 M$). Lo
    que queda propio es probar el arreglo con un replay antes del PR y verificar el
    ahorro después, en local y con Apache-2.0. Da para un nicho, no para una plataforma
    completa contra Langfuse (1).

**Interfaz:**
13. **La accesibilidad estructural está sin hacer**: no hay «saltar al contenido»; el
    grafo tiene nodos enfocables dentro de un `role="img"`; los títulos no siguen una
    jerarquía y hay campos sin etiqueta. Además, el Diagnóstico tarda unos 10 s en
    cargar sin decir nada (4, altos).

**Proceso:**
14. **Hay cosas del proceso que ya no funcionan**: `test_pricing` lleva semanas en rojo
    a propósito, y eso es un reloj, no una regresión. `DECISIONS.md` (224 entradas,
    6.200 líneas) ya no se puede leer, sólo archivar. Y la disciplina de mutaciones se
    declara, pero no queda registrada en ningún sitio (5).

## Orden que propongo

1. **Las cifras falsas** (1 a 4). Es lo único que contradice lo que promete el producto,
   y casi todo son cambios pequeños con su prueba.
2. **Seguridad entre clientes** (5 a 7). Son pocas líneas por hallazgo; la auditoría las
   señala una a una.
3. **El simhash fuera del camino caliente de la ingesta** (9): hacerlo más barato, o
   calcularlo aparte. Y medir la ingesta con volumen.
4. **Mínimos de operación** antes de abrir la nube (8): copias de seguridad, `/metrics`,
   timeouts de ClickHouse, `restart:` y secretos fuera del compose.
5. **La suite aislada** (10): una base por proceso, sin `DROP` ni `CREATE` compartidos,
   y `test_pricing` como aviso en vez de prueba roja.
6. **Distribución** (11): PyPI, el dominio y una pasada real del ciclo con una clave.
   El auditor de mercado lo pondría primero. Aquí va después de 1–5 porque una cifra
   falsa delante del primer usuario sale más cara que llegar tarde.
7. **Accesibilidad** (13) y lo demás de cada informe.

Sobre el alcance, el informe de mercado avisa de que una sola persona no puede defender
una plataforma completa frente a Langfuse, y recomienda centrarse en el ciclo
probar → arreglar → verificar. Es una decisión de negocio, del dueño.

Cada hallazgo lleva su evidencia (fichero:línea o enlace) en su informe. Los de
producto y los dos altos de seguridad se reprodujeron con SQLite fuera del repositorio.

---

## Auditoría 1 — Mercado y negocio

Hecha el 19 de octubre de 2026 sobre `master` (`47e65cd`). Fuentes internas: `CLAUDE.md`,
`README.md`, `docs/HOJA_DE_RUTA.md` §1, §3, §5 y §6, `docs/usuario/`, `cli.py`. Fuentes
externas: las páginas enlazadas, todas consultadas el 19-10-2026 salvo que se diga otra
fecha. Lo que no he podido confirmar va marcado como «no lo sé» o «sin verificar».

### 1. Veredicto

Laplace es un producto técnicamente serio con una cuña de mercado bien elegida (el
**ahorro verificado**: detectar, probar, arreglar y comprobar con el tráfico de después).
Su disciplina de «no afirmar lo que no se sabe» es real y rara en el sector. Como
negocio, hoy no existe: no hay ni un usuario, el paquete no está en PyPI, no hay dominio,
la GitHub App no está registrada, no hay nube que cobrar, y **ninguna pieza del ciclo
diferencial se ha pasado nunca contra una API real** (replay, bot de PR, arreglos de
texto, Stripe y las cuatro pruebas vivas esperan una clave). Mientras tanto, el mercado
se ha consolidado en 2026 en manos de compradores con mucho dinero (ClickHouse, Dynatrace,
Cisco, Palo Alto, CoreWeave), y en octubre Arize sacó un agente que detecta el gasto
evitable y abre el PR con el arreglo, que era el gran diferencial imaginado. Lo que queda
de verdad diferencial es más estrecho: **probar el arreglo con un replay antes del PR y
verificar el ahorro después con el tráfico real**, en local y con licencia Apache-2.0.
Eso basta para un nicho, no para la plataforma completa que se está construyendo.

**Nota: 4/10.** Sube por la cuña y el rigor (6–7 si sólo valorase el producto). Baja
por distribución nula, validación nula contra la realidad, un alcance de plataforma
imposible de defender para una persona frente a Langfuse, y un nombre que no se puede
posicionar.

### 2. Lo que está bien

- **La cuña es la correcta.** Todos miden el coste; casi nadie dice cuánto te ahorraste
  de verdad. «Ahorrado y recuperable» por ejecución, no en totales, y con reaparecidos
  que vuelven a la lista (`README.md:131-149`), es un mensaje que un CFO entiende.
- **El rigor es un activo comercial**, no sólo de ingeniería: el modelo sin tarifa no
  cuesta cero, la tarifa asumida elige el menor ahorro (`README.md:349-406`). Frente a
  cifras de marketing del tipo «ahorra un 80 %», es creíble.
- **Local, gratis y Apache-2.0** (`README.md:613-615`, `docs/usuario/empezar.md`): es el
  único canal barato que tiene un proyecto sin dinero, y está bien montado
  (`pip install` + `laplace ui`, sin cuenta).
- **OpenTelemetry GenAI de entrada**: puede comer trazas de OpenInference/OpenLLMetry sin
  que el cliente cambie su instrumentación (`docs/HOJA_DE_RUTA.md` §3).
- **Margen por cliente** con ingresos de Stripe (D-161 a D-163): pocas herramientas de
  observabilidad lo tienen; es una buena segunda cuña para quien revende IA.
- **La lectura de §6** (complementario a Langfuse, startups de 2.000–100.000 $/mes,
  local gratis) es sensata como punto de partida.

### 3. Problemas, por gravedad

#### Crítico

**C1. Distribución cero: el producto no se puede conseguir.**
- `laplace-trace` devuelve 404 en PyPI hoy (`https://pypi.org/pypi/laplace-trace/json`),
  y `docs/usuario/empezar.md` empieza con `pip install "laplace-trace[ui]"`: la primera
  línea de la documentación falla.
- El repositorio es público desde el 21-09-2026 y tiene 0 estrellas (`gh repo view`).
- No hay dominio ni scope de npm ni GitHub App (`docs/HOJA_DE_RUTA.md` §5).
- El gancho principal de §6, `laplace audit` con informe compartible, **no existe**:
  `packages/sdk-python/laplace/cli.py:221-233` sólo tiene `ui`, `demo` y `replay`.
- Por qué importa: 224 decisiones (`DECISIONS.md`) y ni un usuario que haya dicho si esto
  le sirve. Cada semana sin usuarios es producto construido a ciegas.

**C2. El «ahorro verificado» no está verificado contra la realidad.**
- `docs/HOJA_DE_RUTA.md` §3: el replay contra la API real, los arreglos de texto contra un
  modelo de verdad, las integraciones JS, Stripe y «las 4 pruebas vivas que validan las
  cifras contra la factura del proveedor» esperan todas una clave.
- El bot de PR funciona sólo con token por proyecto; la App no existe (§5).
- Por qué importa: la promesa de venta es que la cifra cuadra con la factura. Hasta que
  una factura real y un `laplace replay` real cuadren, el diferencial es una hipótesis.
  Con una clave de 20 $ se cierra; es lo más barato y lo más urgente de toda la lista.

**C3. El diferencial «detecta y abre el PR» ya lo tiene un rival con 915 M$ detrás.**
- Arize AX **Cost Agent** (octubre de 2026): lee trazas, agrupa el gasto por causa
  (modelo sobredimensionado, llamadas duplicadas, reintentos desbocados, prompts caros),
  estima el ahorro y, con una «skill» de GitHub opcional, abre un PR con el cambio de
  configuración. Sólo para AX Enterprise.
  <https://arize.com/blog/arize-ax-cost-agent/>
- Dynatrace compra Arize por 915 M$ (anunciado el 13-08-2026).
  <https://www.itpro.com/business/acquisition/dynatrace-acquires-observability-firm-arize-in-usd915m-deal>
- Lo que el artículo de Arize **no** dice que haga: probar el arreglo con un replay antes
  del PR ni verificar el ahorro después del despliegue. Ahí sigue el hueco de Laplace.
- Por qué importa: el discurso «el único que abre el PR» ya es falso. Hay que vender
  «el único que lo **demuestra** antes y después», y hacerlo pronto.

#### Alto

**A1. Laplace se presenta como complemento de Langfuse y se construye como su rival.**
- §6 dice «complementario a Langfuse, no rival». Pero el producto tiene trazas, árbol,
  evaluaciones, conjuntos de casos, prompts con versiones y rollback, panel, alertas,
  SAML y SCIM (`README.md:17-35`, D-211): es la plataforma completa.
- Langfuse tiene 36.011 estrellas (`gh api repos/langfuse/langfuse`, 19-10-2026), más de
  2.000 clientes de pago y 26 M de instalaciones del SDK al mes según su bufete
  (<https://www.orrick.com/en/news/2026/01/open-source-llm-observability-langfuse-acquired-by-clickhouse-inc>,
  enero de 2026), y ahora el dinero de ClickHouse (Serie D de 400 M$,
  <https://pulse2.com/clickhouse-400-million-series-d/>).
- Por qué importa: en todo lo que es «table stakes» (trazas, prompts, evaluaciones),
  Laplace pierde siempre. Y para ser complemento de verdad le falta lo esencial: **leer
  las trazas que el cliente ya manda a Langfuse** (u otro), en vez de pedirle que
  instrumente otra vez. Hoy un usuario de Langfuse tiene que meter el SDK de Laplace o un
  segundo exportador OTLP.

**A2. El mercado se ha consolidado en 2026 en manos de gigantes.**

| Rival | Comprador | Fecha | Fuente |
|---|---|---|---|
| Langfuse | ClickHouse | 16-01-2026 | <https://langfuse.com/blog/announcing-acquisition> |
| Helicone | Mintlify (modo mantenimiento, sin altas nuevas) | marzo de 2026 | <https://chatforest.com/reviews/helicone-llm-observability-gateway/> (tercero), banner en <https://www.helicone.ai/pricing> |
| Galileo | Cisco (ahora «Splunk Agent Observability») | anunciado 09-04-2026 | <https://www.splunk.com/en_us/about-splunk/acquisitions/galileo.html> |
| Portkey | Palo Alto Networks (Prisma AIRS AI Gateway) | cerrado 29-05-2026 | <https://pulse2.com/palo-alto-networks-to-acquire-portkey-to-establish-ai-gateway-as-control-plane-for-autonomous-enterprise-agents/amp/> |
| Arize (Phoenix y AX) | Dynatrace, 915 M$ | anunciado 13-08-2026 | <https://www.itpro.com/business/acquisition/dynatrace-acquires-observability-firm-arize-in-usd915m-deal> |
| W&B Weave | CoreWeave (precios en «CoreWeave Forge») | redirección vista hoy | <https://coreweave.com/forge-pricing> |

- Lectura: el mercado vale dinero (bien para un futuro comprador), pero los rivales que
  quedan tienen equipos de ventas y presupuesto para copiar una regla de derroche en un
  trimestre. Hay también una oportunidad concreta: **los clientes de Helicone**, que no
  admite altas y tiene la hoja de ruta congelada (la guía de migración de §6 sigue
  vigente).
- Ironía a vigilar: el almacén de la versión en la nube es ClickHouse, que ahora es dueño
  del rival principal. No es un riesgo técnico (Apache-2.0 del motor), sí de mensaje.

**A3. El repositorio en español cierra el canal open source.**
- Código, comentarios, commits, CLI y logs en español (`CLAUDE.md`, D-148). La interfaz en
  cinco idiomas está bien; el repositorio no.
- Por qué importa: el canal elegido es open source + SDK. Quien evalúa una herramienta de
  desarrollo lee el README y el código, y contribuye o abre issues en inglés. Ningún
  rival relevante tiene el repositorio en otro idioma. El «mercado hispano como cabeza de
  playa» (§6) no tiene ningún dato detrás que yo haya encontrado: no lo sé, y el
  documento tampoco lo justifica.

**A4. La garantía «10 veces lo que pagas o te devolvemos el mes» choca con las reglas
del producto.**
- §6 la propone. Pero lo «ahorrado» es una estimación por ejecución antes/después
  (`README.md:131-143`), medida por el propio Laplace, sin cuadrar todavía con ninguna
  factura (C2). Garantizar una cifra propia no auditada contradice «nunca afirmar lo que
  no se sabe».
- Aritmética: Business (299 $/mes) exige demostrar 2.990 $/mes de ahorro, es decir, un
  15 % de 20.000 $/mes de gasto. Para un cliente del extremo bajo del objetivo (2.000
  $/mes) es imposible; para Team (49 $) exige 490 $/mes, un 25 % de ese gasto.
- Por qué importa: es un pasivo contable y una puerta a disputas, y el día que el ahorro
  se dispute la credibilidad, que es el activo, se va.

**A5. No hay nube que vender.**
- Los planes de §6 (Free 50.000 spans, Team, Business) necesitan multiinquilino, límites
  por plan y cobro propio. En el backend no hay nada de eso: lo único que menciona
  suscripciones es `apps/backend/laplace_backend/stripe_ingresos.py`, que lee los
  ingresos **del cliente** para el margen, no cobra a nadie.
- Por qué importa: la única fuente de ingresos prevista no existe. Operar una nube con
  datos de prompts de terceros además exige SOC 2 o similar para vender a una scaleup;
  todos los rivales de pago lo anuncian (p. ej. Portkey: SOC2 Type 2, GDPR, HIPAA en
  <https://portkey.ai/pricing>).

#### Medio

**M1. El nombre no se puede posicionar.**
- «Laplace» en IA ya lo usan: Laplace AI (Suzhou, plataforma de agentes) y Laplace
  Intelligence (Michigan, red-teaming de robots con agentes, en TechCrunch Disrupt).
  <https://finder.techleap.nl/news/feed/laplace-ai-secures-angel-round-funding>,
  <https://www.winzheng.com/en/article/laplace-intelligence-robot-red-teaming>
- `laplace` en PyPI está cogido (200 en `https://pypi.org/pypi/laplace/json`);
  `laplace.dev`, `.ai` y `.sh` cogidos (§5); `uselaplace.com`, que §5 daba por libre,
  **resuelve DNS hoy**, así que probablemente ya no lo esté (sin confirmar en un
  registrador).
- En buscadores compite con la transformada de Laplace: el SEO de marca es inviable.
- Por qué importa: cambiar de nombre ahora cuesta un `sed`; después del lanzamiento,
  cuesta la tracción.

**M2. La regla estrella pierde valor con el tiempo.**
- El precio por nivel de rendimiento baja entre 9× y 900× al año, mediana 50×
  (Epoch AI, <https://epoch.ai/data-insights/llm-inference-price-trends>; Epoch avisa de
  que las caídas más rápidas son recientes y pueden no durar).
- «Modelo caro» y «contexto fijo» son las reglas más fáciles de explicar y las más
  expuestas: el proveedor abarata, cachea por su cuenta o enruta. Las que resisten son
  las de comportamiento del agente (bucle, repetición, reintentos por JSON, historial que
  crece), porque no dependen de la tarifa. No he encontrado datos fiables de si el
  gasto total por agente sube o baja (más tokens por tarea frente a tarifas más bajas):
  no lo sé.
- Coste operativo: la tabla de precios caduca cada 30 días (`README.md:356-361`). Es
  mantenimiento perpetuo para una persona.

**M3. Los proveedores ya enseñan coste y trazas.**
- Anthropic: API de uso y coste por espacio de trabajo, granularidad diaria.
  <https://docs.anthropic.com/en/api/usage-cost-api>
- OpenAI AgentKit: conjuntos de datos, «trace grading», optimización automática de
  prompts y evaluación de modelos de terceros.
  <https://openai.com/blog/introducing-agentkit> (la fecha del anuncio no la he
  confirmado en la página).
- Lectura: el proveedor no tiene incentivo para decirte «gasta menos en mí» ni para ver
  varios proveedores a la vez; ése es el hueco. Pero «ver lo que cuesta» ya es gratis.

**M4. El tamaño de mercado citable es débil.**
- La cifra más repetida: 2,69 mil M$ en 2026 para plataformas de observabilidad de LLM,
  36,3 % anual (The Business Research Company,
  <https://www.thebusinessresearchcompany.com/report/large-language-model-llm-observability-platform-global-market-report>).
  Es un solo analista con una definición propia; otros dan cifras muy distintas
  (<https://www.meticulousresearch.com/product/ai-observability-market-6879>).
- La porción «optimización de coste de agentes» no la cuantifica nadie que yo haya
  encontrado. Para un inversor, mejor la señal indirecta: seis compras en diez meses.

**M5. El plano de control no es diferencial.**
- Topes duros por clave y por usuario existen gratis en LiteLLM (código abierto,
  <https://docs.litellm.ai/docs/proxy/users>) y en Portkey (presupuestos granulares en
  Enterprise, <https://portkey.ai/pricing>).
- Lo propio de Laplace es aplicarlo en el SDK, sin pasarela, y cortar **bucles**
  (D-187, D-189); útil, pero sólo llega a Python (D-206) y no se vende solo.

#### Bajo

- **B1.** El README dice que la fase 3 (diagnóstico con modelo) está «pendiente»
  (`README.md:31`), mientras la hoja de ruta da por hechos diagnósticos con modelo
  (D-178 a D-182, D-208). Mensaje incoherente para quien llega.
- **B2.** La documentación de usuario es Markdown plano (`docs/HOJA_DE_RUTA.md` §4); sin
  sitio, no hay SEO de documentación, que es el canal número uno de las herramientas
  para desarrolladores.
- **B3.** El SDK de TypeScript espera un scope de npm (§5): la mitad de los agentes en
  producción son Node, y hoy dependen de OpenInference/OpenLLMetry más atributos
  `laplace.*`.

### 4. Comparativa

Leyenda: ✓ sí, con fuente; ~ parcial; ✗ no lo he encontrado; ? no lo sé.
† = conocimiento general del producto, no re-verificado hoy en su documentación.

| | Laplace | Langfuse | LangSmith | Helicone | Arize Phoenix / AX | Braintrust | Datadog LLM Obs. | W&B Weave | Galileo (Splunk) | OpenLIT | Portkey | LiteLLM |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Coste por llamada | ✓ por tramos | ✓ [a] | ✓ [b] | ✓ | ✓ | ✓ [c] | ✓ | ✓† | ✓ [d] | ✓ [e] | ✓† | ✓ |
| Gasto evitable detectado | ✓ 5+ reglas | ✗ | ~ Insights [b] | ✗ | ✓ AX Cost Agent [f] | ✗ | ? | ✗ | ? | ✗ | ✗ | ✗ |
| Replay contrafactual | ✓ (sin pasar contra API real) | ~ datasets + experimentos† | ~ datasets + playground† | ✗ | ~† | ~ playground [c] | ? | ~† | ? | ✗ | ✗ | ✗ |
| PR automático con el arreglo | ✓ (sin App registrada) | ✗ | ✗ | ✗ | ✓ AX Enterprise [f] | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Verificación con tráfico real después | ✓ | ✗ | ✗ | ✗ | ✗ (no lo dice [f]) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Plano de control (tope y parada) | ✓ SDK, sólo Python | ✗ | ✗ | ~ | ✗ | ✗ | ✗ | ✗ | ~ guardrails [d] | ✗ | ✓ [g] | ✓ [h] |
| Margen por cliente | ✓ (Stripe sin probar en real) | ~ coste por usuario [i] | ? | ~† | ✗ | ~ por etiquetas [c] | ? | ✗ | ? | ✗ | ✗ | ~ gasto por usuario [h] |
| Evaluación | ✓ | ✓† | ✓† | ✗ | ✓ | ✓ [c] | ✓† | ✓† | ✓ [d] | ~† | ✗ | ✗ |
| Prompts versionados | ✓ | ✓† | ✓† | ~† | ✓† | ✓ [c] | ✗† | ✓† | ? | ✓† | ✓† | ✗ |
| OpenTelemetry | ✓ | ✓† | ✓ [b] | ✗† | ✓ (OpenInference)† | ✓† | ✓† | ✓† | ? | ✓ [e] | ✗† | ✓† |
| Alojamiento propio | ✓ gratis | ✓ gratis [a] | sólo Enterprise [b] | Enterprise [j] | Phoenix gratis; AX Enterprise [k] | Enterprise [c] | ✗ | Enterprise [l] | ? | ✓ [e] | gateway OSS [g] | ✓ |
| Licencia | Apache-2.0 | MIT + `ee` (NOASSERTION en GitHub)† | propietaria | Apache-2.0 | Phoenix ELv2 [k] | propietaria | propietaria | Apache-2.0 (SDK) | propietaria | Apache-2.0 [e] | gateway MIT | MIT + enterprise† |
| Precio de entrada de pago | — (no existe) | Core 29 $/mes, 100k unidades [a] | Plus 39 $/puesto [b] | Pro 79 $/mes [j] | AX Pro 50 $/mes [k] | Pro 249 $/mes [c] | ~160 $/mes por 100k spans (sin verificar) [m] | Pro desde 60 $/mes [l] | ? (redirige a Splunk) | gratis | Production 49 $/mes [g] | Enterprise a consultar [h] |

Fuentes, todas consultadas el 19-10-2026:
[a] <https://langfuse.com/pricing> ·
[b] <https://www.langchain.com/pricing>; Insights y OTel, terceros:
<https://latitude.so/blog/langsmith-alternatives-agent-observability-2026> ·
[c] <https://www.braintrust.dev/pricing> ·
[d] <https://www.splunk.com/en_us/about-splunk/acquisitions/galileo.html> ·
[e] <https://docs.openlit.io/latest/introduction>, licencia en `gh api repos/openlit/openlit` ·
[f] <https://arize.com/blog/arize-ax-cost-agent/> (octubre de 2026) ·
[g] <https://portkey.ai/pricing> ·
[h] <https://docs.litellm.ai/docs/proxy/users>, <https://www.litellm.ai/pricing> ·
[i] <https://langfuse.com/docs/observability/features/users> ·
[j] <https://www.helicone.ai/pricing> ·
[k] <https://arize.com/docs/phoenix/self-hosting/license>; precios de AX vía terceros:
<https://aicoolies.com/pricing/arize-phoenix> ·
[l] <https://coreweave.com/forge-pricing> ·
[m] terceros, cifras que se contradicen:
<https://www.truefoundry.com/blog/datadog-llm-observability-pricing>; la página oficial
no la pude leer entera.

Otros que no estaban en la lista y compiten en algo: **Opik** de Comet (Apache-2.0,
22.733 estrellas), **MLflow Tracing** (Apache-2.0), **OpenLLMetry** de Traceloop
(Apache-2.0); y en margen por cliente, **Paid.ai** (gratis hasta 100.000 $/año
facturados, según un tercero: <https://nevermined.ai/blog/paid-ai-review>), **Pay-i**
(<https://pay-i.com/llms.txt>, sin precios públicos) y pequeños como Margine o
MarginDash (sin verificar).

#### Diferenciales reales frente a imaginados

**Reales (nadie más que yo haya encontrado):**
1. **Verificación del ahorro con el tráfico de después**, por ejecución, con
   reaparecidos. Es el único punto de la tabla que no tiene nadie. Es *el* producto.
2. **El ciclo completo atado a un hallazgo**: detectar → replay sobre las llamadas
   reales de ese paso → PR → verificar. Las piezas sueltas existen (Arize detecta y abre
   PR; Braintrust y LangSmith reejecutan conjuntos), el ciclo cerrado no.
3. **Todo eso en local, gratis y Apache-2.0.** El Cost Agent de Arize es Enterprise.
4. **Honestidad medible** (tarifa asumida, «no lo sabemos», sin porcentaje con poca
   muestra). Diferencial de marca, no de función: hay que enseñarlo, no se nota solo.

**Imaginados o ya alcanzados:**
- «El único que abre el PR»: falso desde octubre de 2026 [f].
- Coste por llamada, por tramos de caché: lo tienen todos.
- Plano de control: LiteLLM y Portkey lo hacen mejor y en cualquier lenguaje.
- Evaluaciones, prompts, panel, SAML/SCIM: paridad en el mejor caso, por detrás en
  madurez siempre.
- Cinco idiomas: agradable, no decide compras.
- Margen por cliente: Langfuse da coste por usuario [i] y hay startups dedicadas;
  Laplace lo junta con el ahorro, que sí es bueno, pero no es exclusivo.

**Lo que tienen todos y a Laplace le falta:** un paquete instalable, una nube con plan
gratuito, el SDK de TypeScript publicado, integraciones probadas contra APIs reales,
documentación en un sitio con buscador, comunidad (Discord/GitHub), certificaciones
(SOC 2) y casos de clientes con nombre.

### 5. Cliente, canal y precio

**Cliente ideal.** Startups y scaleups con agentes en producción y **10.000–100.000
$/mes** de gasto en modelos, con un responsable técnico que tiene que justificar la
factura. Por debajo de 10.000 $/mes el ahorro absoluto (un 20 % de 2.000 $ son 400 $) no
paga una herramienta: ese tramo es el canal gratuito local, no clientes. Segundo
segmento: quien revende IA a sus clientes y necesita margen por cliente. Esto es una
hipótesis razonable, no un dato: en el repositorio no he encontrado ni una entrevista con
un cliente.

**Canal.** Por orden de coste y de probabilidad:
1. **Una auditoría sin instrumentar**: `laplace audit` sobre lo que el equipo ya tiene
   (un export de Langfuse, un fichero OTLP, o la API de uso de Anthropic/OpenAI) que dé
   en cinco minutos «tiras X $/mes en estos tres sitios». Sin esto, el primer contacto
   pide instrumentar, y nadie instrumenta dos veces.
2. **Open source local** (`pip install`), con el repositorio en inglés.
3. **El bot de GitHub** como bucle viral: un PR firmado por Laplace con el ahorro probado
   lo ve todo el equipo.
4. **Migración desde Helicone** (sin altas nuevas): un público con dolor y fecha.
5. Show HN con un caso real con números (el propio dueño o un socio de diseño), no con
   la demo inventada.

**Precio.** Los niveles de §6 están en la banda del mercado (Langfuse Core 29 $, LangSmith
39 $/puesto, Portkey 49 $, Arize Pro 50 $, Weave 60 $, Helicone 79 $, Braintrust 249 $;
fuentes en la tabla). Recomendaciones:
- **Sin cobro por puesto**: bien, diferencia frente a LangSmith.
- **Medir por spans, no por ahorro.** Cobrar un porcentaje del ahorro es tentador pero
  obliga a que la cifra sea auditable por el cliente y abre disputas; la mayoría cobra
  por unidades ingeridas.
- **Quitar la garantía 10×** o cambiarla por una que no dependa de la cifra propia: «si
  en 30 días no encontramos al menos N hallazgos con dinero, el mes es gratis».
- **Enterprise desde 18.000 $/año** sin SOC 2 ni SSO probado contra un IdP real (D-211
  dice que falta) no se vende. Aplazarlo.
- Dónde está el dinero de verdad para un proyecto así: **ser comprado** (el patrón de
  2026) o un plan de equipo por el ciclo de ahorro encima de la observabilidad que el
  cliente ya tiene. No una plataforma completa.

### 6. Riesgos

| Riesgo | Probabilidad | Impacto | Comentario |
|---|---|---|---|
| Un rival grande copia la verificación posterior | Alta | Alto | Es una consulta antes/después; Arize ya tiene la mitad del ciclo [f]. La ventaja dura meses, no años. |
| Los proveedores lo hacen ellos | Media | Medio | Ya dan coste y evaluación (Anthropic, OpenAI AgentKit). No tienen incentivo a recomendar gastar menos ni a ver varios proveedores. |
| Bajada de precios | Alta | Medio | Erosiona «modelo caro» y «contexto fijo»; las reglas de comportamiento (bucles, reintentos) resisten. |
| Rivales con más dinero | Ya ocurre | Alto | Seis compras en 2026 (tabla de A2). |
| Nombre y dominio | Alta | Medio | Ver M1. Barato de arreglar hoy. |
| Persona única | Alta | Alto | 224 decisiones, siete pestañas, dos almacenes con paridad, cinco idiomas, tabla de precios cada 30 días: la superficie ya supera lo que una persona mantiene mientras vende. |
| Confianza en datos de prompts en una nube nueva | Media | Alto | Mitigado por el modo local; la nube necesita certificación para vender. |

### 7. Lo que falta

- Un solo usuario externo, o un socio de diseño, con su gasto real.
- Una pasada real de extremo a extremo: factura del proveedor = cifra de Laplace;
  replay real; PR real fusionado; ahorro verificado real.
- Paquete en PyPI, dominio, App de GitHub, sitio de documentación.
- `laplace audit` y la ingesta desde lo que el cliente ya usa (Langfuse, OTLP, Helicone).
- Nube con planes y cobro (o la decisión explícita de no tenerla todavía).
- Repositorio público en inglés.
- Un nombre que se pueda buscar.

### 8. Propuestas

| # | Propuesta | Esfuerzo | Impacto |
|---|---|---|---|
| 1 | **Validar contra la realidad ya**: una clave de proveedor y pasar las 4 pruebas vivas, un `laplace replay` real y un PR real del bot en un repositorio propio. Cerrar C2 antes de escribir otra regla. | S | Crítico: convierte el diferencial en hecho. |
| 2 | **Publicar**: reservar el nombre definitivo, PyPI, scope de npm, dominio, App de GitHub (todo §5). | S (del usuario) | Crítico: sin esto no hay canal. |
| 3 | **Decidir el nombre antes del lanzamiento.** Si se queda «Laplace», al menos un nombre de paquete y dominio buscables. | S ahora / L después | Medio-alto. |
| 4 | **`laplace audit` sin instrumentar**: leer un export de Langfuse, un fichero OTLP o la API de uso de Anthropic/OpenAI, y sacar un informe HTML compartible con los hallazgos y su dinero. | M | Alto: es el gancho de §6 y la puerta de entrada de los usuarios de Langfuse y Helicone. |
| 5 | **Ser complemento de verdad**: conector que lea trazas de Langfuse por su API pública (o guía de doble exportador OTLP), y retirar del mensaje la plataforma completa. | M | Alto: alinea el producto con el posicionamiento de §6. |
| 6 | **Congelar la superficie de plataforma** (prompts, evaluaciones generales, SAML/SCIM, panel) y dedicar todo al ciclo de ahorro y a la verificación. | S (decisión) | Alto: foco de una persona. |
| 7 | **Repositorio público en inglés** (README, CLI, logs, mensajes de error); la interfaz sigue en cinco idiomas. Código y commits pueden seguir en español un tiempo. | M | Alto para el canal open source. |
| 8 | **Precio**: quitar la garantía 10×, cobrar por spans, sin puestos; Team 49 $ y Business 299 $ como está; Enterprise sólo con SOC 2. | S | Medio: evita un pasivo y una contradicción de marca. |
| 9 | **Caso público con números reales** (el agente del propio dueño o un socio): «gastaba X, el replay probó Y, el PR se fusionó, en 30 días ahorró Z, y cuadra con la factura». Es el material de Show HN. | M | Alto. |
| 10 | **Guía y comando de migración desde Helicone** (sin altas nuevas desde marzo de 2026). | S-M | Medio: público con dolor y prisa. |
| 11 | **Poner por delante las reglas que resisten la bajada de precios** (bucle, repetición, reintentos, historial) frente a «modelo caro». | S (mensaje) | Medio. |
| 12 | Corregir el estado del README (fase 3) y pasar `docs/usuario/` a un sitio. | S | Bajo. |

Lo que no he podido comprobar: el precio oficial de Datadog LLM Observability, el de
Galileo dentro de Splunk, si LangSmith calcula coste sobre spans OTel, la fecha del
anuncio de AgentKit y si `uselaplace.com` está registrado (sólo que resuelve DNS).

---

## Auditoría de seguridad de Laplace

Auditor: seguridad (2 de 6). Fecha: 2026-10-19. Base: `master` en `47e65cd`.
Alcance: `apps/backend`, `packages/sdk-python`, `apps/web`, `docker-compose.yml`.
Sólo lectura. Dos pruebas de concepto con SQLite en una carpeta temporal fuera del repo
(los resultados se citan abajo); nada contra ClickHouse ni Docker.

### 1. Veredicto

El código está escrito por alguien que piensa en seguridad, y se nota: el middleware
deniega por defecto, los borrados por id van acotados en la propia consulta, hay CSRF
por cabecera propia, SAML va con `python3-saml` en modo estricto y con la aserción
gastada, el webhook de alertas resuelve una sola vez y fija la IP, la ingesta tiene tope
de descompresión, y tokens y sesiones se guardan con hash. No he encontrado inyección SQL
ni XSS explotables. Los fallos reales están en las **costuras entre inquilinos y entre
roles**: un `installation_id` de la GitHub App que no se ata a nadie (otro cliente puede
abrir PR en tu repositorio), escrituras destructivas que no figuran en `ADMIN_WRITES`
(una clave de ingesta filtrada puede vaciar el histórico de un proyecto), y SCIM, que
da de alta en tu organización a cuentas de otras. **Nota: 7/10.** Hay buen diseño y
pocas líneas que tocar, pero dos de los hallazgos son de los que se pagan caro en
cuanto haya más de un cliente en la nube.

### 2. Lo que está bien

- `AuthMiddleware` (`apps/backend/laplace_backend/auth.py:236`) deniega por defecto y
  comprueba `project_id` en la URL y en el cuerpo JSON. Las rutas nuevas nacen
  protegidas. `PUBLIC_PATHS` sólo tiene tres entradas, y las tres están justificadas.
- Los objetos que se buscan por id (anotaciones, conjuntos, trazas) se acotan con
  `scope()` / `_alcance()` en la consulta, no con una comprobación previa
  (`storage/metadata.py:744`, `api.py:221`). He revisado las rutas con `project_id`
  opcional y todas lo acotan.
- Sesiones, tokens de verificación, de invitación y de SCIM, y claves de API: se guarda
  sólo el hash. Contraseñas con scrypt. El login compara en tiempo constante aunque el
  correo no exista (`api_cuentas.py:296`).
- SAML (`saml.py`): modo estricto, aserción firmada, sin SHA-1, audiencia obligatoria,
  `InResponseTo`, `state` atado a una cookie y `ID` de aserción de un solo uso
  (`cuentas.py:1031`). OIDC con PKCE, `state` y `nonce` atados al navegador, y `next`
  saneado (`api_empresa.py:44`).
- SSRF del webhook de alertas: sólo https a IP pública, resolución única con la IP fijada
  y sin redirecciones (`alerts.py:717-760`, `690`).
- Webhook de GitHub: HMAC-SHA256 comparado en tiempo constante, y un secreto vacío no
  firma (`github_bot.py:167`).
- Arreglos con modelo (D-221): lo que escribe el modelo sólo entra como valor de una
  constante, y además se comprueba que el árbol de sintaxis no cambia
  (`arreglos_texto.py:167-197`). Aprobar exige una persona, no una clave
  (`arreglos_texto.py:576`). El texto de las trazas va delimitado como dato
  (`judge.py:138`).
- Replay (D-214): sólo con SQLite (`api_replay.py:42`). Las claves del proveedor se leen
  del entorno y no viajan por la API.
- `LimiteCuerpo` corta por `Content-Length` y por flujo. El gzip de OTLP tiene tope
  (`ingest/otlp.py:67`). Cabeceras `nosniff`, `frame-ancestors 'none'` y
  `Referrer-Policy`, en el backend y en Next.
- La web no usa `dangerouslySetInnerHTML` salvo para un script constante
  (`app/layout.tsx:92`). React escapa todo lo que pinta de las trazas.
- `X-Forwarded-For` sólo se cree de proxies listados, y compose pone Caddy delante para
  que eso sea cierto (`docker-compose.yml`, D-128).

### 3. Problemas, por gravedad

#### ALTO-1. El `installation_id` de la GitHub App no se ata a la organización: cualquier cliente escribe en el repositorio de otro

- Evidencia: `api_ajustes.py:836-839` guarda cualquier `installation_id >= 1` sin
  comprobar de quién es. `github_bot.py:304-318` pide un token de esa instalación con el
  JWT de la App de Laplace. `github_bot.py:461-562` crea la rama, hace el commit y abre el
  PR en `ajustes["repo"]`, que también elige quien llama.
- Ataque: el admin de un proyecto cualquiera (otro cliente de la nube) hace
  `PUT /api/github {"project_id":"mio","repo":"victima/app","installation_id":<id de la víctima>}`.
  Los ids de instalación son enteros correlativos: se enumeran o se ven en una URL de
  GitHub. Después, con `POST /api/github/pull-request` o con `/api/arreglos-texto/aprobar`,
  Laplace usa la App que instaló la víctima para crear ramas `laplace/*` (con
  `force: true` si ya existían, `github_bot.py:538-544`) y abrir PR en su repositorio
  privado. El contenido del PR sale de trazas y hallazgos del atacante. Además, el flujo
  lee el árbol y los ficheros del repositorio de la víctima (`arreglos_texto.py:307`),
  con lo que sirve para confirmar si existen rutas y literales: **a comprobar** cuánto
  se filtra en los mensajes de error.
- Gravedad: alta. Pasa a **crítica** si la nube usa la GitHub App
  (`LAPLACE_GITHUB_APP_ID`): es un acceso de escritura entre inquilinos con la identidad
  de Laplace, y un PR del bot de Laplace es justo lo que la víctima espera revisar con
  confianza.

#### ALTO-2. Escrituras destructivas o sensibles fuera de `ADMIN_WRITES`: una clave de ingesta filtrada, o un simple miembro, vacía el histórico

- Evidencia: `auth.py:68-77` sólo pide admin para seis rutas. Faltan
  `PUT /api/retention` (`api_ajustes.py:663`), `DELETE /api/subjects` (`:677`),
  `PUT /api/stripe` y `POST /api/stripe/sync` (`:273`, `:296`),
  `PUT /api/customers/revenue` y `PUT /api/exchange-rates` (`:201`, `:239`) y
  `POST /api/github/pull-request` (`:1254`). Ninguna de ellas comprueba el rol por su
  cuenta. El propio código dice que las claves de proyecto «viven en el entorno de los
  agentes de los clientes, que es donde se filtran» (`auth.py:396`).
- Prueba de concepto (SQLite, `LAPLACE_AUTH_REQUIRED=true`, clave del proyecto
  `victima`): `DELETE /api/projects` da **403**, pero `PUT /api/retention {"days":1}`,
  `DELETE /api/subjects?customer_id=acme&confirm=acme` y `PUT /api/stripe` dan **200**.
- Ataque: con la clave sacada de un `.env`, de un log o de una imagen de contenedor,
  `days=1` hace que el bucle diario (`retencion.py:aplicar_si_toca`) borre todo lo
  anterior a ayer. Borrar sujetos uno a uno destruye datos de forma selectiva. Cambiar la
  clave de Stripe por una del atacante falsea los márgenes por cliente. Un «miembro» de
  la organización puede hacer lo mismo.
- Gravedad: alta (pérdida de datos irreversible, desde la credencial más expuesta).

#### MEDIO-1. SCIM da de alta a cuentas de otras organizaciones, las renombra y revela su nombre

- Evidencia: `cuentas.py:1124-1152` (`scim_alta`) busca el correo en toda la instalación
  y, si existe, hace miembro a esa cuenta sin invitación y sin restringirse a los dominios
  de la organización. `scim.py` `replace_user` y `patch_user` llaman a
  `cuentas.renombrar`, que cambia el nombre global de la cuenta.
- Prueba de concepto (SQLite): con un token SCIM de la organización A,
  `POST /scim/v2/Users {"userName":"ceo@victima.com"}` → **201**. La respuesta trae el
  nombre real y la fecha de alta de la cuenta, que pertenece a la organización B. La
  víctima pasa a ser miembro de A. Después,
  `PUT /scim/v2/Users/{id} {"displayName":"…"}` → **200**, y la víctima aparece
  renombrada también en B.
- Más: un correo que todavía no existe queda creado sin contraseña, y no hay forma de
  restablecerla. Cuando su dueño acepte una invitación legítima, `accept`
  (`api_cuentas.py:447`) llama a `comprobar_contrasena` con `None`, que lanza
  `AttributeError` y devuelve un 500: la cuenta queda secuestrada por adelantado.
- Gravedad: media (enumeración de cuentas, cuentas metidas en organizaciones ajenas sin
  consentimiento, altas bloqueadas). Lo puede hacer cualquier admin de una organización,
  porque es quien crea los tokens SCIM.

#### MEDIO-2. SSRF en el descubrimiento OIDC

- Evidencia: `sso.py` `_get`/`_pedir` sólo exigen `https://` en la URL inicial.
  `urllib` sigue redirecciones, también a `http://`, sin mirar si la IP es interna, y el
  error devuelve hasta 300 caracteres del cuerpo de la respuesta
  (`SSOError(... cuerpo)`). `api_empresa.py:338` se lo enseña al admin con
  `error.sso_emisor`.
- Ataque: el admin de una organización pone como emisor
  `https://atacante.example/x`, que redirige con un 302 a
  `http://10.0.0.5:8123/?query=…` o a cualquier servicio interno. Con eso escanea puertos
  por el tiempo de respuesta y por el mensaje de error, y lee el principio de las
  respuestas de error internas. El webhook de alertas ya tiene la defensa buena
  (`alerts.py:resolver_destino`); ésta no la usa.
- Gravedad: media en la nube (ClickHouse por HTTP en 8123, Postgres y el metadata del
  proveedor de nube quedan al alcance de la red del backend).

#### MEDIO-3. El admin de una organización suplanta al propietario y a cualquier miembro por SSO

- Evidencia: `PUT /api/org/sso` (`api_empresa.py:316`) sólo pide admin de la
  organización y deja cambiar emisor, certificado y protocolo. `usuario_sso`
  (`cuentas.py:1014-1018`) enlaza por correo cualquier cuenta que ya sea miembro.
- Ataque: un admin (rol que, por diseño, no puede ascender a nadie a propietario,
  `cuentas.py:655`) pone su propio IdP y firma una aserción con
  `email=propietario@empresa.com`. Entra como el propietario, o como el administrador de
  la instalación si también es miembro con ese dominio. Los dominios los aprueba la
  instalación, pero el IdP lo elige el admin de la organización.
- Gravedad: media (escalada de privilegios dentro de la organización; afecta a toda la
  instalación si el operador es miembro de una organización cliente).

#### MEDIO-4. Modo local sin comprobar `Host`: DNS rebinding contra `laplace ui`

- Evidencia: no hay `TrustedHostMiddleware` (`main.py:339-363`). En local,
  `auth_enforced` es falso a propósito (`config.py:123`). El replay toma `endpoint` de
  `request.base_url`, es decir, de la cabecera `Host` (`api_replay.py:57`).
- Ataque: el desarrollador tiene `laplace ui` abierto en `127.0.0.1` y visita una web
  maliciosa que hace rebinding de su dominio a `127.0.0.1`. Esa página lee todas las
  trazas (prompts y respuestas de los usuarios finales), borra proyectos y lanza un replay
  de hasta 1000 USD (`api_replay.py:30`) con la clave de OpenAI o de Anthropic del
  desarrollador. **A comprobar** en un navegador real. Por el código, no hay nada que lo
  impida.
- Gravedad: media (requiere una víctima con la UI abierta, pero expone datos de terceros
  y dinero).

#### BAJO

- **B-1. El secreto del webhook de GitHub sale en una lectura.** `GET /api/github`
  devuelve `webhook_secret` (`github_bot.py:150`) a cualquier lector o clave de
  proyecto. Con él se forjan avisos `pull_request` y `deployment_status` que dan
  hallazgos por arreglados y mueven la frontera de despliegue (D-204).
- **B-2. Webhook sin protección de repetición.** No se guarda `X-GitHub-Delivery`
  (`api_ajustes.py:849`). Un aviso firmado capturado se puede reenviar. El efecto es
  casi idempotente, pero `revisar_versiones(forzar=True)` se ejecuta cada vez.
- **B-3. Secretos en claro en la base.** El token de GitHub, la clave de Stripe y el
  `client_secret` de OIDC se guardan tal cual en ajustes y en `sso_configs`
  (`api_ajustes.py:843`, `cuentas.py:913`). Una copia de seguridad o una lectura de
  Postgres los entrega todos.
- **B-4. Sin límite de peticiones en lo que cuesta dinero.** `POST /api/judge`,
  `/api/traces/{id}/diagnosis` y `/api/arreglos-texto/proponer` gastan la clave del
  operador y basta una clave de proyecto para lanzarlos. El único freno que existe es el
  del login (`cuentas.py:170`).
- **B-5. Bloqueo de cuentas por correo.** Fallar logins contra un correo bloquea a su
  dueño durante la ventana (`api_cuentas.py:285`). Es el comportamiento estándar, pero
  sin un aviso ni un desbloqueo.
- **B-6. Exención por prefijo.** `ruta.startswith("/api/org")` (`auth.py:262`, `:330`)
  también exime a una ruta futura como `/api/organizacion-x`, que se saltaría la
  comprobación de alcance y de rol. Hoy no existe ninguna; es una trampa latente.
- **B-7. `accept` con una cuenta sin contraseña** (SSO o SCIM) devuelve un 500 en vez
  de un 403 (`cuentas.py:109-121` sólo captura `ValueError` y `TypeError`).
- **B-8. Valores por defecto de compose.** Las contraseñas `laplace` de ClickHouse y
  Postgres están escritas en `docker-compose.yml:10,36,58`, y el backend publica
  `8000`/`4318` en todas las interfaces. Está bien para desarrollo; es peligroso si
  alguien despliega con ese fichero.

### 4. Lo que falta

- **Redacción de datos personales desactivada por defecto.** `capture_content=True` y
  `redact=None` (`packages/sdk-python/laplace/config.py:70,100`): por defecto, los
  prompts y respuestas de los usuarios finales llegan y se guardan en claro, sin cifrado
  a nivel de aplicación. D-181 existe, pero hay que encenderla.
- **No hay forma de recuperar la contraseña.** Además de ser una carencia de producto,
  agrava MEDIO-1.
- **No hay lockfile de Python.** `pyproject.toml` sólo pone mínimos (`fastapi>=0.110`,
  `python3-saml>=1.16`, `tree-sitter>=0.23`). Las versiones del `.venv` actual están
  bien (starlette 1.6.0, python3-saml 1.16.0, lxml 6.1.3, xmlsec 1.3.17,
  cryptography 50.0.2), pero la imagen de Docker resuelve lo que haya el día del build.
  La web sí tiene `package-lock.json` (Next 16.3.6, React 18.3.1). No he podido
  comprobar CVE sin red; **a comprobar** con `pip-audit` y `npm audit`.
- **No hay CSP con `script-src`**: sólo `frame-ancestors`. Hoy no hay XSS que la
  necesite, pero las trazas son contenido de terceros y una CSP estricta es barata.
- **No hay limitación de peticiones** en la ingesta ni en la API, más allá del tamaño
  del cuerpo.
- **La auditoría no registra** los cambios de retención, Stripe o GitHub
  (`cuentas.anotar` no se llama en esas rutas). Sí registra los borrados de sujetos y
  de proyectos.

### 5. Propuestas

| # | Propuesta | Esfuerzo | Impacto |
|---|-----------|----------|---------|
| 1 | Al guardar `installation_id`, `GET /app/installations/{id}/repositories` (o `/repos/{repo}/installation`) y exigir que el repo esté en esa instalación **y** que la instalación la haya dado de alta esta organización (flujo `setup_url` de la App con `state`). Sin eso, rechazar. | M | Cierra ALTO-1 |
| 2 | Añadir a `ADMIN_WRITES`: `PUT /api/retention`, `DELETE /api/subjects`, `PUT /api/stripe`, `PUT /api/exchange-rates`, `PUT /api/customers/revenue`. Y que `POST /api/github/pull-request` exija una persona, como `aprobar`. Con una prueba por ruta en `test_auth`. | S | Cierra ALTO-2 |
| 3 | `scim_alta`: sólo correos de los dominios aprobados de la organización. Una cuenta existente de otra organización, nunca. `renombrar` sólo si la cuenta no pertenece a otra organización. | S | Cierra MEDIO-1 |
| 4 | Pasar `sso._pedir` por la misma `resolver_destino` + `post_a_ip` del webhook, sin redirecciones, y no devolver el cuerpo de la respuesta al cliente. | S | Cierra MEDIO-2 |
| 5 | Cambiar el emisor o el certificado del SSO: exigir propietario (o instalación) y avisar por correo al propietario. Opcional: no enlazar por correo a propietarios ni a `is_admin`. | S | Mitiga MEDIO-3 |
| 6 | `TrustedHostMiddleware` con `127.0.0.1`/`localhost` cuando la instalación es local, y el `endpoint` del replay fijo desde la configuración y no desde `Host`. | S | Cierra MEDIO-4 |
| 7 | Quitar `webhook_secret` del GET para quien no sea admin; deduplicar `X-GitHub-Delivery` durante 24 h. | S | B-1, B-2 |
| 8 | Cifrar los secretos guardados con una clave de entorno (`cryptography` ya es dependencia: Fernet). | M | B-3 |
| 9 | Frenos por proyecto en las rutas que gastan modelo, reutilizando `FrenosEnBase`. | S | B-4 |
| 10 | Lockfile (`uv lock` o `pip-compile`) para el backend y la imagen, más `pip-audit` y `npm audit` en CI. | S | Cadena de suministro |
| 11 | `redact` encendido por defecto en el SDK para correo, teléfono, tarjeta e IBAN, con la opción de apagarlo explícitamente. | M | Datos personales de terceros |
| 12 | `startswith("/api/org/") or ruta == "/api/org"` en `auth.py`. Y capturar `AttributeError` en `comprobar_contrasena`. | S | B-6, B-7 |

Orden sugerido: 2, 1, 3 y 4 (todas S o M, y cierran lo explotable hoy); después 6, 5 y
7; el resto, con la fase de endurecimiento.

---

## Auditoría 3 — Eficiencia y escala

Auditor: eficiencia y escala. Sólo lectura; nada contra ClickHouse ni Docker. Lo que no
está en una decisión lo medí yo en local, con CPU y SQLite (microbenchmarks sobre
`parse_spans` y el redactor del SDK, en un portátil Windows, Python 3.11). Lo que no se
ha medido lo digo así.

### 1. Veredicto

Laplace está **bien diseñado para leer y mal medido para escribir**. Las lecturas del
Diagnóstico se han medido con una honradez poco común (D-177, D-197, D-212, D-222,
D-223) y se han ido atacando por orden de peso. Pero el camino de **ingesta nunca se ha
medido con volumen** (`scripts/carga.py` mete los spans con SQL y se salta la ingesta,
D-197), y al medirlo yo salta un problema serio: la huella `simhash` de D-219 es un bucle
de Python puro que cuesta unos 11 ms por llamada de 2 KB y **crece con el tamaño del
prompt**, y llega a ser el 90 % del coste de ingesta. Con un único proceso uvicorn y el
GIL, 10 M de spans/día (116/s de media) no entran ni de lejos si hay llamadas con
contextos largos. A eso se suma una caché del Diagnóstico que, con un cálculo de 60–180 s
y una vida de 60 s, **no puede funcionar** (se recalcula siempre y a veces varias veces a
la vez), y una suite que comparte base con la carga y hace ~7.000 `ALTER` sobre la tabla
de producción de pruebas. El plan en curso (D-224 a D-227) ataca bien el coste por lectura
pero deja sin tocar lo que de verdad decide si el cliente de 10 M/día es viable.

**Nota: 5 / 10.** Por encima de la media en método y disciplina (medir antes, paridad,
pruebas que muerden). Por debajo porque hoy un cliente de 10 M/día no se puede servir
con lo que hay: ingesta no medida y con un coste desbocado, Diagnóstico de 7 días a 1–3
min, y nadie sabe lo que ocupa el disco con payloads reales.

### 2. Lo que está bien

- **Método de medida**: cada decisión de escala trae tabla, máquina, ruido y "lo que queda,
  medido y no hecho". D-212 se atrevió a decir "medido, no arreglado" y a reconocer que
  el objetivo de 1,5 s ya no se cumple. D-222 corrigió una atribución de D-212 (los 3,8 GiB
  no eran lo crudo, eran componer `pre_historial`).
- **Preagregados por hora con sustitución** (D-177): idempotentes frente a reenvíos y
  `recalcular_coste`, con "hora sucia → lectura cruda con la misma selección". Es el
  diseño correcto para ReplacingMergeTree.
- **Ingesta fuera del bucle de eventos** y `async_insert` con `wait_for_async_insert`
  (`api.py:79`, `clickhouse.py:880-893`): el 200 significa guardado. Registro de proyecto
  cacheado (`metadata.py:603`).
- **Esquema de `spans`**: ZSTD(3) en payloads, `LowCardinality` en lo repetitivo, clave
  `(proyecto, hora, traza, span)` y retención por TTL (`clickhouse.py:2299`).
- **SDK**: el control cada 30 s va en un hilo daemon y la llamada sólo lee una variable
  (`_control.py:27-31`); `flush` con tope (`_tracer.py:60`). La web exportada pesa 2,5 MB.
- Los filtros de memoria de D-222/D-223 (4 GiB → 0,7 GiB; 2,5 → 0,9 GiB) son reales y
  tienen prueba que muerde.

### 3. Problemas

#### CRÍTICO

**C1. `simhash` domina la ingesta y crece con el prompt** — `ingest/otlp.py:273-297`.
Por cada llamada a modelo recorre todas las palabras de los mensajes que no son de sistema
y, por cada trigrama, un bucle de 64 iteraciones en Python (`for bit in range(64)`).
Medido por mí (`parse_spans`, 500 spans LLM sintéticos, sólo CPU):

| Prompt (entrada+salida) | `parse_spans` por span | simhash |
|---|---|---|
| ~2 KB | **11–13 ms** | ≈ 90 % del total (cProfile: 2,13 s de 2,92 s en 200 spans) |
| ~20 KB | **83–91 ms** | idem |

Sin `simhash` el resto de `_build_span` queda en unos 3 ms por span (con la sobrecarga de
cProfile). Es lineal en palabras, así que un agente que reenvía un contexto de 50 k tokens
en cada vuelta pagaría del orden de **segundos de CPU por span** (extrapolado, no medido).
Va en `run_in_threadpool` pero es Python puro: **retiene el GIL** y compite con todo lo demás
del proceso. D-219 midió el umbral de bits, no el coste de calcularla. Impacto: a 116
spans/s de media, sólo con un 30 % de llamadas LLM de 20 KB son ~3 núcleos de CPU, y uvicorn
es un solo proceso (`apps/backend/Dockerfile:26`, sin `--workers`). Se cae por ahí antes
que por ClickHouse.

**C2. La ingesta no se ha medido nunca con volumen.** `scripts/carga.py` inserta con SQL
(D-197: "los spans entran con SQL, sin pasar por la ingesta"). `test_ingesta_escala.py`
prueba mecanismos (no bloquear el bucle, `async_insert`, registro único), no rendimiento.
No hay cifra de spans/s por proceso, ni de latencia p99 del `POST /v1/traces`, ni de
cuántas partes crea ClickHouse. C1 y H4 se le escaparon por esto. Impacto: toda estimación
de coste de nube para 10 M/día (sección 6) tiene la ingesta como incógnita.

#### ALTO

**H1. La caché del Diagnóstico no aguanta un cálculo más largo que su vida.** TTL 60 s
(`config.py:138-141`); el Diagnóstico de 7 días tarda 59–181 s (D-212). `leer` devuelve
`None` en cuanto pasan 60 s (`cache_diagnostico.py:57`), y `_diagnostico` calcula sin
ninguna coalescencia de peticiones iguales (`api.py:300-317`): *N* pestañas o recargas a la
vez lanzan *N* cálculos completos, cada uno hilo de pool y consultas pesadas contra
ClickHouse (el estudio de D-212 muestra 3,8 GiB en una sola consulta; D-223, 2,5 GiB).
Impacto: con volumen, la caché es un adorno y el primer visitante tras un minuto de calma
espera 1–3 min. Es el patrón exacto de "stampede".

**H2. El renovador mantiene saturado ClickHouse tras un vistazo.** Renueva a los 45 s
(`RENOVAR_A = 0.75`) cada entrada leída en los últimos 30 min (`cache_diagnostico.py:40-44`).
La clave incluye `(proyecto, días, gestiona, idioma, estados)` (`api.py:299-301`): quien
mira 1, 7 y 30 días en dos idiomas crea 6 entradas que se recalculan en bucle durante
media hora, aunque nadie vuelva a mirar. Con cálculo ≥ 45 s el renovador está **siempre**
calculando (`una_vuelta` es secuencial, `cache_diagnostico.py:122-131`). El docstring lo
justifica con "unos 4 s" de D-142; ya no es verdad. Impacto: un único usuario curioso
consume la CPU de ClickHouse de forma continua; mezclado con la ingesta y las fusiones, es
la receta de D-142 (Docker caído).

**H3. El Diagnóstico entero es la unidad de caché y de cálculo; no se calcula por partes.**
`overview()` ejecuta todas las lecturas en serie (`insights/motor.py:598-610`, `detect`),
D-177 demostró que en paralelo no ayuda con 4 núcleos, y se sirve o todo o nada. `/api/graph`
bloquea hasta que termina el Diagnóstico entero (`api.py:415`, D-203) cuando sólo necesita
los ids de hallazgo para marcar nodos; `getOverview` se pide desde 5 pantallas
(`apps/web`: inicio, trazas, traza, prompts, evaluaciones). `/api/panel` y la ficha de un
hallazgo (`api.py:335`, `insights/motor.py:915`) **no pasan por la caché**: cada clic en
un hallazgo del historial recalcula (38 s a 7 días según D-212). Impacto: la latencia
percibida depende de la lectura más lenta y todo se invalida a la vez ante cualquier cambio.

**H4. `pre_sucias` recibe una fila por span, no por hora.** `clickhouse.py:894` pasa
`{(s.project_id, s.start_time) for s in spans}` (microsegundos) y `preagregados.marcar`
(`preagregados.py:393-406`) inserta una fila por elemento aunque `hora_de` las deje todas
iguales. Con 500 spans por lote son 500 filas idénticas en `(proyecto, hora)`; a 10 M/día,
10 M de filas/día en una tabla ReplacingMergeTree que luego se lee con `max(marca)` por
hora (`preagregados.py:446, 559`) en cada lector. El efecto no se vio porque la carga no
pasa por aquí (C2). Por medir: crecimiento de la tabla y coste de esa lectura sin fusionar.
Arreglo de una línea (`hora_de(s.start_time)`), pero con su prueba.

**H5. La suite es frágil por diseño: base compartida y DDL sobre la tabla "de verdad".**
Detalle en sección 5. Resumen: ~24 de los 36 ficheros con ClickHouse usan la base por
defecto, `migrate()` se llama 164 veces y cada una ejecuta ~43 `ALTER` sobre `spans`
(`clickhouse_schema.sql`, 10 son `MODIFY COLUMN … CODEC`), y hay pruebas que hacen
`ALTER TABLE spans DROP COLUMN` (`test_migracion_nube.py:89`) o `OPTIMIZE … FINAL`
(`test_migrar_orden.py:216`) sobre esa misma tabla.

**H6. Redacción del SDK en el hilo del agente.** `FiltroProcessor.on_end` redacta de forma
síncrona (`_filtro.py:236, 312`) con 7 regex por cada atributo de texto
(`Redactor.texto`, `_filtro.py:206`). Medido por mí: **3,6 ms con 2 KB, 31 ms con 20 KB,
340 ms con 200 KB** (≈ 1,5 ms por KB), y 51 ms con 28 KB con PII. `max_payload_bytes` es 1 MiB
(`config.py:18`): un span de 1 MiB cuesta ~1,5 s del hilo de la aplicación. Una llamada a
modelo tarda segundos y lo absorbe, pero una traza con 10–20 spans con payloads de 20 KB
añade 0,3–0,6 s a la ejecución del agente, y las herramientas rápidas (milisegundos) se
ven multiplicadas. Está apagada por defecto (no es el caso sin `redact=`), pero es
justo la opción que activa quien maneja datos personales, o sea, el cliente grande.

#### MEDIO

**M1. Sin topes de consulta en el cliente ClickHouse.** No hay `max_execution_time`,
`max_memory_usage` ni `max_threads` en `clickhouse.py` (Grep: el único `settings=` es el de
`insert_spans`). `days` llega hasta 90 (`api.py:263`). El Diagnóstico de 90 días a 10 M/día
no se ha medido nunca (lo máximo medido son 7 días); extrapolando linealmente de D-212,
sería del orden de 13× el de 7 días, y la consulta del historial ya pide cientos de MiB.
D-142 ya tumbó Docker una vez. Impacto: un `?days=90` de un cliente (o un bucle del
navegador) puede tumbar el nodo.

**M2. Memoria del SDK con muestreo: acotada por trazas, no por bytes.** `FiltroProcessor`
guarda hasta 2.000 trazas × 5.000 spans abiertos (`_filtro.py:~345`) hasta que cierra la
raíz, y cada `ReadableSpan` lleva el payload entero (hasta 1 MiB). Cota teórica: gigabytes.
En la práctica un agente típico no llega, pero un proceso que mantiene trazas largas
abiertas (un agente conversacional) sí. Además `BatchSpanProcessor` va con los valores por
defecto (cola de 2.048 spans; sin límite de bytes): 2.048 × 20 KB ≈ 40 MB, hasta 2 GiB con
spans de 1 MiB. No hay medida de memoria en ningún sitio ("cuánta memoria usa" no está
medido; sólo hay cotas por lectura del código).

**M3. El sondeo de control sin jitter.** `_control.py:37, 156` pregunta cada 30 s por
proceso, sin dispersión. Con 1.000 procesos son 33 req/s a `/api/control`, y tras un
despliegue arrancan sincronizados. Coste bajo por petición, pero hay que contar cuánto
cuesta esa ruta (autenticación + lectura de metadatos) antes de decir que es gratis.

**M4. Cada `migrate()` lanza ~43 `ALTER` al arrancar.** También en producción: cada
arranque del backend (y cada réplica) los repite sobre la tabla grande. `MODIFY COLUMN
… CODEC` es sólo metadatos en versiones recientes de ClickHouse, pero toma bloqueo de
tabla y espera a mutaciones y fusiones pesadas en curso. Con una tabla de 70 M de filas y
`DELETE` ligero pendiente, es lo que "se queda sin tiempo" según `HOJA_DE_RUTA.md:67`.
No medido.

**M5. Las tablas de registro de ClickHouse crecen sin tope en `master`.** D-212: `text_log`
1,2 GiB, `processors_profile_log` 630 MiB y 90–130 % de CPU en reposo. El arreglo está
en curso en el worktree (D-224, `deploy/clickhouse/registros.xml`) pero no en `master`:
mientras tanto toda medida y toda prueba corre con ese ruido.

**M6. Cierre de `DELETE` ligero en cargas y pruebas.** D-222: 75 M de filas físicas para 3,5 M
vivas tras `--borrar`. La consecuencia práctica es que cualquier prueba o medida posterior a
una carga lee 20× más de lo que cree (la causa más probable de las "casi 10 horas").

#### BAJO

- **B1.** `dedup_hash`, `loop_hash` y `loop_out_hash` serializan el mismo payload con
  `json.dumps(sort_keys=True)` tres veces (`otlp.py:221, 239`, ~0,5 ms por llamada con 2 KB).
  Una sola serialización las compartiría. Menor, pero es gratis tras arreglar C1.
- **B2.** Dos viajes a ClickHouse por lote (spans + `pre_sucias`), cada uno con
  `wait_for_async_insert=1`: latencia del 200 = suma de dos esperas del búfer asíncrono
  (por defecto del orden de cientos de ms). Medir antes de tocar.
- **B3.** La web no para el sondeo de `LIVE_INTERVAL_MS` (5 s, `useLive.ts:24`) cuando la
  pestaña está oculta (no hay `visibilityState` en `app/` ni `lib/`), y `boton-replay.tsx:62`
  sondea cada 1,5 s. Sólo con el modo en vivo/replay activos y pidiendo 50 trazas
  (`/api/traces?limit=50`); impacto bajo. Todas las lecturas van con `cache: "no-store"`
  (`api.ts:161`) y no hay deduplicación de `/api/overview` entre componentes de una misma
  pantalla.
- **B4.** Chunk JS mayor 330 KB sin comprimir (`out/_next/static/chunks`, 28 ficheros,
  1,8 MB en total; gzip no medido). No es un problema.

### 4. Lo que falta

- Una **prueba de carga de la ingesta**: `POST /v1/traces` real con lotes de 500 spans, 2 y
  20 KB, midiendo spans/s por proceso, p99, partes creadas y filas de `pre_sucias`.
- **Payloads realistas** en `scripts/carga.py`. D-212 midió 6,1 GiB para 72,5 M de spans
  (≈ 90 B/span), que es el disco de spans casi vacíos. Con prompts reales el disco por span
  es entre 10× y 100× mayor. **No se sabe el disco real de 10 M/día.**
- Una medida de **memoria del SDK** (RSS con cola llena, con y sin muestreo y redacción) y de
  la **latencia añadida por llamada** del envoltorio (`tracemalloc` + `perf_counter` con un
  cliente simulado). Hoy no hay ninguna.
- Medida del Diagnóstico a 30 y 90 días, y con **dos peticiones concurrentes**.
- Un presupuesto: qué Diagnóstico de 7 días es aceptable (¿10 s?, ¿60 s?). D-212 sólo dice
  que 1,5 s "ya no se cumple"; sin objetivo no se sabe cuándo parar.
- Límites de recursos en `docker-compose.yml` y de consulta en el cliente.
- Medida del `/api/control` (coste por petición) y de `/api/graph` aparte del Diagnóstico.

#### Sobre el plan en curso (D-224 a D-227, piezas 1, 3, 4, 5 de D-212)

**Bien enfocado** en lo que ataca: las tres lecturas crudas (70 M de filas cada una), las
candidatas de bucles/repeticiones y los recuentos/medianas son el 70–80 % del tiempo
medido, y el TTL de registros (5) es barato y quita ruido a todo lo demás. El orden es
correcto, y cada pieza tiene su medida.

**Lo que se le escapa:**
1. **No hay objetivo de latencia** ni se cambia la unidad de cómputo: aun bajando de 100 s a
   30 s, la caché de 60 s y el renovador siguen mal (H1, H2). Sin coalescencia y sin
   renovación acotada, cada mejora de cálculo se paga igual en carga concurrente.
2. **El Diagnóstico sigue siendo un monolito** (H3): `/api/graph` y la ficha no se benefician.
3. **La ingesta** (C1, C2, H4) queda fuera por completo y es el cuello que antes se rompe.
4. No hay **topes de consulta** (M1): las piezas reducen el coste típico, no el peor caso.
5. Las medidas se harán en el mismo portátil de 16 hilos y 9,7 GB con ruido; D-212 ya
   advierte que no valen para el contenedor de 4 núcleos. Hace falta una máquina de
   referencia fija (la de 4 núcleos).

### 5. La suite: por qué tarda 10 h en vez de 21 min

Datos del repositorio:
- 36 ficheros de prueba construyen un `ClickHouseStore`; sólo 12 crean su propia base
  (`CREATE DATABASE prueba_…`, p. ej. `test_preagregados.py:62`). Los otros ~24 usan la
  base por defecto, **la misma donde `scripts/carga.py` deja 70 M de spans**.
- `migrate()` se llama 164 veces en las pruebas (`grep "\.migrate()"`); cada una repite
  ~43 `ALTER TABLE` (`clickhouse_schema.sql`).
- Hay DDL destructivo sobre la tabla compartida: `test_migracion_nube.py:89` (`DROP COLUMN
  IF EXISTS` en `spans`), `test_migrar_orden.py:216` (`OPTIMIZE TABLE spans FINAL`).
- Con `-n 2 --dist loadfile` dos procesos hacen `ALTER`/`DROP` sobre la misma tabla mientras
  otros leen e insertan; los `ALTER` esperan a los bloqueos de las lecturas pesadas y
  de las fusiones. `HOJA_DE_RUTA.md:67` ya lo dice: "con ellos [los datos de la carga] en la
  base, la suite tarda horas y los `ALTER` de las pruebas se quedan sin tiempo".
- Los `DELETE` ligeros de `--borrar` no liberan filas físicas (D-222: 75 M físicas / 3,5 M
  vivas): cada `FINAL` y cada `ALTER` sigue pagando por las filas muertas.
- Sin tiempo máximo por prueba (`pyproject.toml`: no hay `pytest-timeout`): un bloqueo
  consume horas en vez de fallar en minutos.

No he podido reproducirlo (no se puede tocar ClickHouse); la causa es una inferencia
sólida de lo anterior, no una medida.

**Cómo dejaría de ser frágil:**
1. **Una base por prueba o por módulo** para todas, con una fábrica única en `conftest.py`
   (`CREATE DATABASE prueba_<uuid>` + `DROP … SYNC`) y prohibir `Settings()` por defecto en
   tests de nube (un test que lo compruebe).
2. Una **instancia de ClickHouse de pruebas** separada de la de las cargas (otro puerto o
   proyecto de compose), de modo que carga y suite no compartan nada.
3. **`pytest-timeout`** de 120–300 s por prueba y `max_execution_time` en el cliente de pruebas.
4. `migrate()` de `ALTER` sólo si hace falta (comprobar `system.columns`/`system.data_skipping_indices`
   antes), para el arranque real y para las pruebas.
5. Salir de `--borrar`: `DROP DATABASE` de una base de carga propia (`carga_<fecha>`).

### 6. Coste de tenerlo en la nube: cliente de 10 M de spans/día

Todo es estimación con lo medido; las incógnitas se marcan.

| Recurso | Estimación | Base |
|---|---|---|
| **Ingesta** (116 spans/s de media; pico ×3–5 incierto) | Hoy: > 1 núcleo sólo en `parse_spans` con un 30 % de LLM y prompts de 2 KB, ~3 núcleos con 20 KB; no cabe en un proceso con GIL. Tras arreglar C1: del orden de 0,3–0,5 núcleo | mis microbenchmarks; el 30 % de LLM es supuesto |
| **Disco ClickHouse** | **Desconocido.** Con payloads casi vacíos: ≈ 0,9 GB/día (D-212: 6,1 GiB / 72,5 M). Con prompts reales comprimidos con ZSTD(3), un orden de magnitud o dos más: del orden de 10–100 GB/día; con retención de 30 días, 0,3–3 TB | supuesto sin medir; hay que probar con payloads reales |
| **Disco extra** | Índice `ngrambf_v1(4, 65536…)` sobre todo el contenido (`clickhouse_schema.sql`, `idx_contenido`): coste de CPU en cada inserción y de disco por gránulo; no medido | |
| **Memoria ClickHouse** | Picos de 0,7–0,9 GiB por Diagnóstico de 7 días (D-222/D-223), 2–3 simultáneos por la falta de coalescencia (H1) → ≥ 3 GiB sólo en consultas, más fusiones. 16 GB de RAM es el mínimo sensato; no medido en el contenedor real | |
| **CPU ClickHouse** | El Diagnóstico de 7 días consume 59–181 s con 16 hilos (D-212); el renovador (H2) lo repite cada 45 s por entrada vista. Con 4 núcleos (D-177) el 7 días era 6 s con la versión de entonces y hoy no se ha vuelto a medir. Con el cálculo actual, **un cliente así necesita 8–16 núcleos dedicados sólo para mirar el panel** | D-212 |
| **Backend API** | 1 vCPU por cada ~90 spans/s con 2 KB hoy (11 ms/span); con C1 arreglado ≈ 4× más | mis medidas |
| **Postgres/metadatos** | Insignificante en ingesta (registro cacheado) | `metadata.py:603` |

Es decir: **lo que cuesta no es almacenar sino calcular el panel**. Con el diseño actual
el cliente de 10 M/día necesita una máquina de ClickHouse de 8–16 núcleos y 16–32 GB para
que la ingesta y el Diagnóstico convivan, y eso es antes de saber el disco real.

### 7. Propuestas concretas

| # | Propuesta | Esfuerzo | Impacto |
|---|---|---|---|
| P1 | **Arreglar `simhash`**: limitar a las primeras *N* palabras (p. ej. 2.000; ya se compara con umbral de 4 bits), o contar bits con `numpy`/operaciones sobre enteros en vez de 64 iteraciones por trigrama; calcularla sólo si hay una regla que la use. Con su prueba de coste (en rojo hoy) y de que el resultado no cambia hasta el tope | S | Quita ~90 % del coste de ingesta (C1) |
| P2 | **Prueba de carga de la ingesta real** (`POST /v1/traces`, 2 y 20 KB, 500 spans/lote) en `scripts/carga.py`, con spans con payloads realistas; medir spans/s, p99, partes, filas de `pre_sucias`, disco/span | M | Cierra C2 y la incógnita del disco |
| P3 | **Arreglar `marcar`**: una fila por (proyecto, hora) (`hora_de` antes de deduplicar); prueba de que un lote de 500 spans deja 1 fila | S | Quita H4 |
| P4 | **Coalescer el cálculo de la caché** (un `Future` por clave; los demás esperan) y servir el valor viejo mientras se recalcula (stale-while-revalidate, TTL de servicio de varios minutos); renovar sólo la ventana por defecto y con un tope de entradas y de CPU | M | Quita H1 y H2: carga constante y acotada |
| P5 | **Topes de consulta** al cliente de lectura (`max_execution_time`, `max_memory_usage`, `max_threads`) y límite de `days` por plan; medir 30 y 90 días | S | Evita el repetido D-142 (M1) |
| P6 | **Calcular por partes**: cachear cada lector por (proyecto, ventana, hora cerrada) o servir el Diagnóstico en dos tiempos (resumen + pasos rápidos ya, lecturas lentas después); `/api/graph` y la ficha, sobre la caché | L | Latencia percibida baja de minutos a segundos aunque el cómputo siga tardando (H3) |
| P7 | **Suite**: una base por prueba, instancia separada de la carga, `pytest-timeout`, `migrate()` con comprobación previa, `--borrar` por `DROP DATABASE` | M | Pasa de 10 h a ~21 min de forma estable (H5, M4, M6) |
| P8 | **SDK**: redactor con una sola regex combinada o con atajo si no hay `@`/dígitos, límite de tamaño de lo que se redacta con cola asíncrona; `BatchSpanProcessor` con límites por bytes; jitter en el sondeo de control | M | Quita H6, M2, M3 |
| P9 | **Uvicorn con varios procesos** (`--workers N`) *después* de P1, y comprobar que el renovador y los preagregados no se multiplican (hoy corren dentro de cada proceso: el bucle de preagregados ya lo prevé, la caché no) | S | Escala la ingesta por núcleos |
| P10 | Mergear el TTL de registros (D-224) cuanto antes, y limitar los recursos del contenedor en `docker-compose.yml` | S | Quita M5 |
| P11 | Fijar **un objetivo de latencia** del Diagnóstico por ventana (p. ej. 1 día < 5 s, 7 días < 30 s) y una máquina de referencia de 4 núcleos | S | Dice cuándo parar el plan de D-212 |

**Orden recomendado:** P1 → P3 → P5 → P4 (todo S/M y de alto retorno), luego P2 y P7, y
dejar P6 para cuando D-224–D-227 aterricen y se pueda medir qué queda.

*No comprobado en esta auditoría:* nada contra ClickHouse; no medí `/api/control`,
`/api/graph` ni el SDK real contra un cliente de modelo; los números de ingesta son de
spans sintéticos con texto de palabras repetidas (el coste por KB de `simhash` con prosa
real puede variar, pero su forma —lineal y por trigrama en Python— no).

---

## Auditoría 4 · Interfaz, estilo y accesibilidad

Fecha: 2026-10-19. Revisado con la versión local (SQLite, `laplace ui` + `laplace demo`,
800 ejecuciones en 30 días) y Playwright/Chromium: Diagnóstico, Panel con grafo, Trazas y
una traza, Clientes, Prompts, Ajustes, Probar (evaluaciones), la ficha de un hallazgo y un
404; 1440 y 375 px, tema oscuro y claro (claro sólo en escritorio; en móvil sólo oscuro),
y Diagnóstico en francés (375) y chino (1440). Tabulador y DOM medidos por script.

**No cubierto:** el replay y la aprobación de arreglo con propuesta real (la demo no tiene
repositorio de GitHub conectado: la ficha sólo muestra «Conecta un repositorio…»), el alto
contraste, WebKit/Firefox, un lector de pantalla real y los contrastes del tema claro más
allá de lo que ya mide `test_tema.py`. Capturas fuera del repo (carpeta temporal).

### 1. Veredicto

Es una interfaz bastante más cuidada que la media de un producto de este tamaño: la
jerarquía del Diagnóstico responde en cinco segundos a «cuánto puedo ahorrar» (cifra
grande, barra de trabajo real frente a evitable, lista ordenada por dinero), el vocabulario
es llano, el producto no promete lo que no sabe y la base de accesibilidad (foco visible de
2 px en todo el recorrido, `aria-pressed`, `role="img"` con etiqueta, sin desbordes
horizontales en 375 px) está hecha a propósito. Lo que le falta no es diseño sino
terminar: **faltan cosas de accesibilidad estructural** (sin enlace «saltar», sin regiones
vivas, jerarquía de títulos incoherente, el grafo con hijos enfocables dentro de un
`role="img"`), **el estado de carga dura ~10 s y es sólo un esqueleto mudo**, y el
**Panel y el Diagnóstico acumulan demasiado texto explicativo** para quien paga la factura.
Los cinco idiomas están al día, con fallos puntuales de gramática («de les 6,9 derniers
jours») y un «6,9 días» que no debería existir.

**Nota: 7,5 / 10.** Alta en claridad de producto, coherencia y honestidad de las cifras;
penalizada por la accesibilidad estructural pendiente (por debajo de un AA estricto en 2–3
criterios), el tiempo de carga percibido y el exceso de texto en Panel/Ajustes.

### 2. Lo que está bien

- **Diagnóstico**: una sola cifra dominante («Puedes dejar de pagar hasta 184 US$ al mes»),
  su desglose («108 de trabajo real / 184 que estás tirando»), y debajo tres hallazgos
  ordenados por dinero con «qué cambiar» y «cuántas ejecuciones afecta». Los demás,
  plegados («Ver 5 más», «Y 5 que no cuestan dinero, sólo tiempo»).
- **Honestidad en pantalla**: «Confianza media · 2 notas», «proyección a 30 días de los
  últimos 7», el gráfico del Panel que deja en blanco los tramos sin ejecuciones y lo
  explica, y la regla de «no es coste cero». Eso es raro y valioso.
- **Foco**: de 14 tabulaciones seguidas, 13 con `outline: solid 2px`; sólo el `summary` de
  «Confianza media» usa el anillo por defecto (1 px `auto`).
- **Responsive**: `scrollWidth == innerWidth` en las 9 pantallas a 375 px, también en fr;
  el coste de Trazas ya se ve en móvil (el A2 de la auditoría anterior está resuelto).
- **Temas**: oscuro y claro coherentes, tokens en `00-temas.css`, alto contraste y tema
  del sistema como opciones, script de restauración sin fogonazo (`layout.tsx:71`).
- **Etiquetas ARIA** útiles: barra «108 US$ de coste necesario, 184 US$ evitable», gráfico
  «Coste por ejecución por tramo», «Grafo del agente con 9 pasos».
- **Traducciones**: 5 catálogos casi paralelos (≈1095 claves); sólo `aj.al.webhook_titulo`
  coincide con el inglés sin traducir en fr/pt. El chino es natural y conciso.
- Moneda y números con formato propio (`format.ts`) en vez de `Intl`, y el dinero siempre
  en mono tabular alineado en tablas.

### 3. Problemas

#### Seguimiento de la auditoría anterior (`docs/auditoria-rediseno.md`)

| Hallazgo | Hoy |
|---|---|
| A1 «Esperando la primera ejecución» con trazas antiguas | No reproducible aquí (demo con datos al día); no verificado con datos antiguos |
| A2 coste fuera de pantalla en móvil | **Resuelto** (columna COSTE visible) |
| A3 ámbar en claro sin AA | **Resuelto** (cifras ocres oscuras legibles) |
| B0, C2 | Resueltos según su propio documento; no remedido |
| B3 «En todas las ejecuciones» en mono grande | **Resuelto**: ahora «8 de cada 10 ejecuciones» en texto normal |
| C1 «$14 , 64» | **Resuelto**: la cifra grande ya va en Inter (y la coma se lee bien) |
| C3 comillas anidadas | **Sigue igual**: «Usas el modelo caro…: «responder — “Eres el asistente de Vuelos Lapl…”» · gpt-5.6-terra» (ver M1) |
| B1 nav recortada a ~1000 px | No revisado; a 1440 y 375 la navegación cabe |
| B2 texto obsoleto «Cuando exista la capa de evaluación» | **Resuelto** (ahora dice «Probar te dirá…») |

#### Alto

**A1 · Carga larga y muda.** Diagnóstico tarda ~10 s en pasar de esqueleto a contenido
(medido con SQLite y 860 trazas, con otras sesiones usando la máquina: puede exagerar,
pero lo hacen 375 y 1440 por igual). Las capturas a 2,5 y 5 s seguían en esqueleto, en las
tres pantallas pesadas. El esqueleto no dice qué espera ni anuncia nada a lectores de
pantalla (no hay ninguna `aria-live`/`role=status` en la página). Importa porque es la
primera impresión del producto. Propuesta: render progresivo (hero en cuanto llegue
`/api/overview`, la lista después), `role="status"` + `aria-busy` en el contenedor y, pasado
~3 s, una línea «Calculando el diagnóstico de 7 días…». Esfuerzo S–M.

**A2 · Sin enlace «Saltar al contenido» (WCAG 2.4.1).** Para llegar al primer hallazgo con
teclado hacen falta 14 tabulaciones (barra, selector, 7 pestañas, rango, idioma, Avanzado,
Confianza…). Propuesta: `<a class="skip" href="#contenido">` como primer hijo de `body`
en `app/layout.tsx:80` y `id="contenido"` en `<main>`; visible sólo con foco. S.

**A3 · El grafo del agente: nodos enfocables dentro de `role="img"` (WCAG 4.1.2).**
`components/GrafoAgente.tsx:195` marca el SVG como `img`; los 9 nodos (`:243`, `tabIndex 0`,
`aria-pressed`) quedan dentro, y los lectores tratan los hijos de `img` como presentacionales:
el teclado llega, pero el lector de pantalla no anuncia nada o anuncia «imagen» y nada más.
Además las flechas con importes («×456 · 2,35 US$», «×248 · 13,11 US$») se solapan entre sí
y con las líneas (Panel, 1440 px). Propuesta: quitar el `role="img"` del SVG y darle
`role="group"` + `aria-label`; cada nodo `role="button"` con `aria-label` completo («responder,
gpt-5.6-terra, 256 llamadas, 0,79 US$»); y además una tabla visible o `<details>` «ver como
lista» con los mismos datos (origen, destino, llamadas, coste), que también ayuda a quien
ve el grafo apretado. M.

**A4 · Jerarquía de títulos incoherente (WCAG 1.3.1, 2.4.6).** Trazas empieza en `h2`
(`app/trazas/page.tsx:108`, sin `h1`), Ajustes empieza en `h2` («Ajustes de «demo»»); la
pantalla de traza no tiene `h1`; Diagnóstico mete tres tarjetas laterales como `h2`
(«CLIENTES», «PRESUPUESTO», «COBERTURA») antes de «13 cosas que arreglar», que es el título
que importa,. Además `<main>` no existe en
Trazas ni en la traza (los marcadores de `landmarks` salen sin `MAIN`). Propuesta: un
`h1` por pantalla (puede ser `.sr-only` si el diseño no quiere uno visible), `main` en todas
y las tarjetas laterales como `aside` con `aria-label`. S.

**A5 · Etiquetas de formulario faltantes.** El campo de correo de alertas
(`app/ajustes/alertas.tsx:83`) no tiene `<label>`, `aria-label` ni `aria-labelledby`: sólo el
placeholder «equipo@empresa.com». Los demás campos de Ajustes sí tienen etiqueta. S.

#### Medio

**M1 · Títulos de hallazgo ilegibles.** «Usas el modelo caro para respuestas cortas:
«responder — “Eres el asistente de Vuelos Lapl…”» · gpt-5.6-terra»: tres niveles de comillas,
un fragmento del prompt cortado a media palabra y el nombre del modelo, en una sola frase de
20+ palabras que es el título de la primera tarjeta y de la ficha. Con un prompt largo la
línea es el elemento más pesado de la pantalla. Propuesta: título corto estable («Modelo caro
en respuestas cortas»), y debajo, en mono pequeño, el paso («responder»), el modelo
(`gpt-5.6-terra`) y el prompt (`Eres el asistente de…` con `title=` completo). Lo mismo
en el CSV. S–M.

**M2 · «6,9 días» (y «6,9 derniers jours», «过去 6.9 天»).** La ventana de «7 días» acaba en
el instante actual y el cálculo la expone con decimales en el texto de los dos primeros
bloques del Diagnóstico («al ritmo de los últimos 6,9 días»). Un usuario que eligió «7 días»
ve «6,9»: se lee como un error. Propuesta: mostrar el rango elegido («7 días»), no el
medido, o redondear a entero salvo menos de 1 día. `ventana.dias_*` en `lib/mensajes/*.ts`
y quien lo formatea. S.

**M3 · Exceso de texto en Panel y Ajustes.** Panel: antes del primer gráfico hay un titular
(«Suben las dos cosas…»), un párrafo de cuatro líneas, un subtítulo, otro párrafo («Lo que
cuesta una ejecución de tu agente. Es la cifra que importa…»), seis tarjetas, nota del
gráfico y «Totales, como contexto: Van aquí abajo a propósito». El mensaje útil es una
frase («el gasto sube un 140 %: 41 % más ejecuciones y cada una cuesta un 70 % más»). Los
mensajes más largos del catálogo son de Ajustes (`aj.gh.webhook.texto` 442 caracteres,
`aj.control.alcance` 333). Propuesta: una frase por bloque, el resto detrás de «¿Cómo se
calcula?» (ya existe el patrón en el Diagnóstico) y mover los textos de Ajustes a un
`details`. M.

**M4 · Cabecera en móvil: ocupa 190 px y en francés pierde el proyecto.** Tres filas
(proyecto, controles, siete pestañas en 3 filas) antes del contenido a 375 px. En francés
(`fr-FR`) el selector de rango se superpone al logo y a la barra «/», y el nombre del
proyecto desaparece. Propuesta: pestañas en una fila con scroll horizontal (o menú),
selector de proyecto con prioridad de ancho mínimo, y ajustes de rango/idioma dentro de
un único botón «Vista». `components/TopBar.tsx`, `10-responsive.css` (regla que hace
ceder al proyecto). M.

**M5 · Cabecera «Probar» y nombres de pestaña por idioma.** «Probar» es el nombre en la
barra, pero la pantalla se titula «Probar» y la ruta es `/evaluaciones` y en la ficha se
dice «conjunto de casos» y «Evaluaciones»; un usuario nuevo no sabe que «Probar» es donde
prueba un modelo más barato. En zh, «测试» y «诊断» sirven, pero «追踪» para Trazas es ambiguo
(«rastreo»). Propuesta: subtítulo corto bajo el título de Probar («Comprueba que un
modelo más barato responde igual») y misma palabra en todas partes. S.

**M6 · (sin verificar) Proyecto inexistente.** Con `?project=nada` mi captura salió en
esqueleto y no llegué a ver el estado final; comprobar que no confunde «proyecto desconocido» con «sin trazas». Propuesta: distinguir «proyecto
desconocido» (con enlace al selector) de «sin trazas todavía» (A1 de la auditoría anterior).
S.

**M7 · Trazas móvil: 50 filas de golpe.** La captura a 375 px mide 6.113 px de alto: la
lista es el único sitio sin paginación ni «cargar más», y cada fila es un bloque de
3 líneas de texto muy cortado («¿Cuánto cuesta una …»). Propuesta: 20 filas + «ver más»,
y mostrar nombre completo con `line-clamp-2`. S.

#### Bajo

- **B1 · Sin `<h1>`/título único por pantalla en `document.title`:** Diagnóstico y estados
  vacíos se llaman sólo «Laplace»; las demás «Panel — Laplace». Poner «Diagnóstico — Laplace»
  y el nombre del proyecto en el título del documento. S.
- **B2 · `aria-live` ausente** en acciones que cambian la pantalla (marcar como arreglado,
  «Ver en vivo», guardar presupuesto). Añadir una región `role="status"` común. S.
- **B3 · Movimiento:** hay `prefers-reduced-motion` en 4 hojas (`09`, `12`, `22`, `02`
  según recuento), pero transiciones/animaciones en 7 (`06-arbol`, `08-trazas`,
  `19-lista-del-inicio` no las tienen). Una regla global en `01-base.css` que apague
  `animation`/`transition` bajo la media-query cubre todo. S.
- **B4 · Objetivos táctiles:** un enlace/botón por pantalla mide menos de 24×24 px (WCAG
  2.5.8) y en Probar, 3; revisar enlaces en línea y el control «Ver en vivo». S.
- **B5 · Tema de partida:** sin elección guardada se usa el oscuro y se ignora
  `prefers-color-scheme` (decisión D-155). Es defendible, pero es lo contrario de lo que
  espera quien tiene el sistema en claro: valorar «sistema» como valor por defecto y
  dejar el oscuro como producto de marca sólo si se elige. S.
- **B6 · Mensajes de francés:** `lib/mensajes/fr.ts:13` `"les {n} derniers jours"` produce
  «de les 6,9 derniers jours» (debe ser «des»; el artículo se pega a «de»), y «ce mois-ci»
  se parte en «mois-/ci» en móvil. «Vous avez déjà cessé de payer 2,22 $US depuis que vous
  avez corrigé un problème» suena a traducción literal (mejor: «Vous économisez déjà 2,22 $US
  par mois depuis votre correction»). `fr` es además un 10 % más largo que `es`; revisar
  cortes en móvil. `aj.al.webhook_titulo` sin traducir en fr y pt. S.
- **B7 · Cifras:** el patrón `$` vs `US$` convive: «184 US$» (es/fr: «$US»), «US$184»
  (zh), es coherente por idioma; no comprobé Clientes/Prompts símbolo a símbolo. Los importes de `format.ts` son
  buenos: dos decimales hasta 10 céntimos, intervalos con «hasta». No hallé ningún caso de
  «0,00» donde deba ser «no lo sabemos» en las pantallas revisadas. Sin acción salvo
  una prueba que cruce `moneyShort`/`money` por pantalla. S.
- **B8 · Etiquetas de uso interno visibles:** `Cobertura` («Sólo el 29 % de tus llamadas con
  versión de prompt») es un aviso sin acción: añadir enlace «Cómo versionar» (Prompts). S.
- **B9 · Seam del fondo en capturas full-page** (el degradado termina a ~870 px y corta
  el fondo): probablemente artefacto de captura de página completa, pero comprobar que
  `background-attachment` no deja una banda en pantallas altas (Panel, Trazas). S.

### 4. Lo que falta

- Un recorrido de «usuario nuevo» tras `laplace demo`: una única tarjeta «Empieza aquí»
  (1. Mira tu mayor ahorro, 2. Pruébalo en Probar, 3. Conecta GitHub). Hoy el camino está
  repartido entre el hero, «Ver cómo arreglarlo» y Ajustes.
- Atajos de teclado / paleta de comandos (g d, g t…) para el perfil ingeniero.
- Región `aria-live` común, enlace «saltar» y `h1`/`main` coherentes (A2, A4, B2).
- Prueba automática de accesibilidad: `test_pantallas.py` comprueba errores de consola y
  desbordes; no hay axe/pa11y ni comprobación de etiquetas, `h1`, landmarks ni tamaño de
  objetivo. Un test Playwright de 30 líneas con los chequeos del apartado A cierra el hueco
  sin instalar nada nuevo.
- Estado de error visible: no pude forzar uno limpiamente; comprobar que un 500 de la API
  no deja el esqueleto para siempre (cargas de ≥10 s ya lo hacen difícil de distinguir).

### 5. Propuestas, por orden

| # | Qué | Esfuerzo | Impacto |
|---|---|---|---|
| 1 | Enlace «Saltar», `main` e `h1` en todas las pantallas; tarjetas laterales como `aside` (A2, A4) | S | Alto (WCAG 2.4.1, 1.3.1) |
| 2 | Grafo: quitar `role="img"`, nodos con `role="button"`/`aria-label`, tabla alternativa y etiquetas de flecha sin solape (A3) | M | Alto |
| 3 | Carga: render progresivo, `role="status"`, mensaje a los 3 s (A1) | S–M | Alto (primera impresión) |
| 4 | `label` del correo de alertas y prueba que exige etiqueta a todo `input` (A5) | S | Medio |
| 5 | Título corto del hallazgo + detalle en mono (M1) | S–M | Alto en claridad |
| 6 | «7 días», no «6,9 días» (M2) | S | Medio (parece un fallo) |
| 7 | Recortar el texto del Panel y de Ajustes a una frase + «¿Cómo se calcula?» (M3) | M | Medio |
| 8 | Cabecera móvil compacta; arreglo del solape en francés (M4) | M | Medio |
| 9 | Corregir `fr.ts:13` («de les» → «des») y la frase del ahorro; revisar «Trazas»/«Probar» (B6, M5) | S | Bajo–medio |
| 10 | Regla global `prefers-reduced-motion`; objetivos táctiles ≥24 px; `document.title` por pantalla (B1, B3, B4) | S | Bajo |
| 11 | Prueba Playwright de accesibilidad estructural en `test_pantallas.py` (apartado 4) | S | Evita regresiones |
| 12 | Tarjeta «Empieza aquí» tras la demo y paginación de Trazas (M7) | M | Medio |

**Qué no tocaría:** la jerarquía del Diagnóstico, la política de «no lo sabemos», el
formato de dinero en `format.ts` ni los tokens de tema: son lo mejor de la interfaz.

---

## Auditoría 5 — Código, pruebas y operación

Auditor: sesión de lectura (sin ejecutar nada contra ClickHouse/Docker). Base: `master` en `47e65cd`.
Limitación: no he corrido la suite ni medido tiempos; lo de las pruebas lentas es inferencia desde el código.

### 1. Veredicto

Laplace es un código inusualmente disciplinado para ser casi todo obra de un asistente: decisiones documentadas, pruebas de nube que corren de verdad en CI (ClickHouse + Postgres), Dockerfile sin root, PyPI por Trusted Publishing, `/health` real y 503 reintentable cuando el almacén falla. Lo que lo frena para producción **no es la calidad del código sino la operación**: no hay copias de seguridad, ni métricas, ni alertas sobre Laplace mismo, ni política de reinicio, ni timeouts en el cliente de ClickHouse (probable origen de las 10 h de suite). Y el proceso (DECISIONS.md de 430 KB) ya cuesta más de lo que rinde como memoria. Con los fallos de operación arreglados sería un 8; hoy:

**Nota: 6,5 / 10.** Código y pruebas 8; proceso 6; operación en la nube 4,5 (no desplegable con datos que importen todavía).

### 2. Lo que está bien

- CI real (`.github/workflows/ci.yml`): ruff, pruebas con ClickHouse y Postgres, `LAPLACE_EXIGIR_NODE` para que nada se salte en silencio, `tsc`, `npm audit`, dos builds de la web, pantallas en Chromium, `compose config`.
- `paquete.yml` construye los wheels, los instala en limpio y prueba `laplace ui`; publica con Trusted Publishing, sin tokens guardados (`paquete.yml:94-102`).
- Ingesta: fallo del almacén → 503 y el exportador reintenta, escritura idempotente (`api.py:93-100`, ReplacingMergeTree). Rechaza el lote entero si la clave no es del proyecto (`api.py:85-92`).
- Marcas de pytest declaradas y las desconocidas son error (`apps/backend/pyproject.toml`, `filterwarnings`); `LAPLACE_EXIGIR_NODE` contra el «salta solo y nadie lo nota».
- Deuda explícita casi inexistente: 1 `shortcut:/TODO` en todo el backend; 224 decisiones numeradas con índice generado (`scripts/indice_decisiones.py`) y prueba que lo vigila (`test_decisiones_indice.py`).
- Migración de la clave de ordenación en ClickHouse con comprobación de disco, partición a partición, reanudable (`storage/migrar_orden.py:139+`), y el arranque *avisa* en vez de copiar solo (`clickhouse.py:869-876`).
- Compose: puertos de bases ligados a 127.0.0.1, `X-Forwarded-For` reescrito por Caddy, explicado.

### 3. Problemas

#### CRÍTICO

**C1. No hay copias de seguridad de nada.** `grep` de «backup/copia de seguridad» en `docs/`, `README.md`, `STATUS.md`, `docker-compose.yml` y `scripts/`: cero. Los datos viven en dos volúmenes Docker (`docker-compose.yml:~150, clickhouse-data / postgres-data`). Postgres tiene **las cuentas, claves de API, ajustes cifrados, SSO/SCIM**; perderlo es perder a los clientes, no sólo datos reanalizables. ClickHouse sin réplica ni backup: un `docker compose down -v` o un disco lleno es irreversible. *Por qué importa:* es el único fallo que no se arregla después.

**C2. Cero observabilidad de Laplace mismo.** Sin `/metrics`, sin Prometheus/Sentry (búsqueda en `laplace_backend/`: nada), sólo `logging.basicConfig` a texto plano (`main.py:224`). No hay forma de saber: spans/s ingeridos, 503 devueltos, latencia de consultas, atraso de preagregados, tamaño de partes en ClickHouse. Un producto de observabilidad que no se observa. Las alertas a Slack (`alerts.py`) son *para el tráfico del cliente*, no para el servicio.

**C3. El cliente de ClickHouse no tiene timeouts ni límites de consulta.** `clickhouse.py:830-837` sólo pasa host/puerto/usuario/clave/base/secure; ni `connect_timeout`, ni `send_receive_timeout`, ni `max_execution_time` (grep: nada). Con `wait_for_async_insert=1` (`clickhouse.py:~884`) y el valor por defecto de la librería (≈300 s de lectura), un ClickHouse colgado o con fusiones atrasadas **retiene cada hilo del pool de FastAPI hasta 5 min**; el pool (≈40) se agota y la API entera, incluido `/health`, deja de responder aunque Postgres esté bien. Es la hipótesis más plausible (junto con C4) de la suite de 10 h en lugar de 21 min.

#### ALTO

**A1. La suite es frágil por diseño con ClickHouse compartido.** 13 ficheros hacen `CREATE DATABASE` / `DROP DATABASE … SYNC` propios (`test_carga.py:45,51`, `test_migrar_orden.py:65,70`, `test_preagregados.py:62,69`, `test_regla_cache_compartida.py:63,69`, …) y otros ~59 ficheros usan ClickHouse. `DROP … SYNC` espera a que terminen las fusiones y mutaciones de esa base; con `-n 2` y un portátil, dos DDL a la vez más el `OPTIMIZE`/mutaciones de otras pruebas se bloquean entre sí y no hay `pytest-timeout` (no figura en `dev`, `pyproject.toml`). Una prueba colgada = suite colgada, sin fallo ni traza. *Propuesta:* ver P1.

**A2. Reinicios, despliegue y migraciones en producción.** `docker-compose.yml`: sin `restart:` en ningún servicio (grep: nada) → tras un reinicio del host, todo queda parado. Migraciones al arrancar (`main.py: auto_migrate → store.migrate()`, `clickhouse.py:858`): con varias réplicas del backend corren a la vez sin bloqueo (`CREATE … IF NOT EXISTS` aguanta, `preagregados.actualizar_esquema`/`marcar_todo_si_nuevo` no es obvio que sea idempotente concurrente). Postgres usa `migrate()` propio (`postgres.py:130`, `cuentas.py:438`, `metadata.py:566,1264`): sin herramienta de versiones ni historial de migraciones aplicadas; no hay rollback. `migrar_orden` es manual y sin plan de ejecución en caliente documentado más allá del docstring.

**A3. Secretos y credenciales por defecto en compose.** `laplace/laplace` para ClickHouse y Postgres escritos en el compose y en los DSN (`docker-compose.yml:10-12, 38-40, 58-60`); el backend publica `8000:8000` y `4318:8000` en **todas** las interfaces (`docker-compose.yml:~100`), con auth «auto». Para la nube hay que sacar todo a un `.env`/gestor de secretos y no publicar 8000 (sólo 4318 tras TLS). El Caddyfile sirve `:3000` en claro; el cambio a dominio con HTTPS está sólo en un comentario. Sin rotación de claves documentada para el cifrado de ajustes (no localizada la clave maestra: confirmar de dónde sale).

**A4. `test_pricing` en rojo «a propósito» no es sano.** Lo rojo es `test_la_tabla_no_esta_caducada` (`test_pricing.py:66-71`, `MAX_AGE_DAYS = 30`): es un *reloj*, no una regresión. Una prueba permanentemente roja (a) enseña a ignorar rojos, (b) tapa fallos nuevos del mismo fichero, (c) impide que CI en `master` esté verde: CI ejecuta `pytest -q apps/backend/tests` sin excluirla, así que o el CI está rojo semanas o no lo mira nadie. D-201 ni siquiera está escrita en `DECISIONS.md` (sólo se «reserva» en `CLAUDE.md:32`): una reserva de número sin entrada. *Propuesta:* P2.

**A5. DECISIONS.md ya no sirve como lectura, sólo como archivo.** 224 entradas, 6.222 líneas, 430 KB; el propio `CLAUDE.md` prohíbe leerlo entero. Está desordenado (D-228 en la línea 6095 antes de D-222 en 6127; huecos de numeración 224-227, 201), y el índice generado pesa 97 KB, otro fichero que no se lee. Resultado: el contexto de cada sesión depende de grep, y las decisiones contradictorias pasan desapercibidas. Los choques de numeración con sesiones en la nube son un coste real (la advertencia está en `CLAUDE.md`).

#### MEDIO

**M1. Documentos de traspaso desfasados.** `docs/HOJA_DE_RUTA.md:3-5` dice «Última actualización: 3 de octubre … D-190 a D-192» y manda leer «D-168 a D-177», cuando el último commit es del 19-oct con D-223. El git log dice «hoja de ruta al día con D-222/D-223» pero la cabecera no cambió: o se actualiza el cuerpo y no la cabecera, o nada. `STATUS.md` 716 líneas.

**M2. Ficheros desmesurados y duplicación entre almacenes.** `storage/clickhouse.py` 2.417 líneas (clase `ClickHouseStore` de ~1.500, `clickhouse.py:825`), `storage/sqlite.py` 2.299, `metadata.py` 1.416, `cuentas.py` 1.337, `api_ajustes.py` 1.260 (52 KB), `base.py` 1.210, `alerts.py` 1.206, `preagregados.py` 1.120, `insights/motor.py` 933. La paridad ClickHouse/SQLite se mantiene *reescribiendo cada consulta dos veces* y comprobándola con `test_paridad.py` (sólo ~18 pruebas) más las pruebas de reglas que corren en ambos. Un método nuevo en `SpanStore` (55 métodos en `base.py`) obliga a tocar 3 ficheros grandes. No hay ciclo grave visible, pero `base.py` actúa de imán (todo el mundo importa `Window`, `SpanStore`).

**M3. Hilo por petición para todo el trabajo de almacén.** Toda lectura/escritura pasa por `run_in_threadpool` + cliente ClickHouse por hilo (`clickhouse.py:845-855`). Es correcto pero hace del tamaño del pool el límite global de concurrencia (ver C3); no hay límite por petición ni backpressure en la ingesta salvo el 503.

**M4. `except Exception` amplios.** 53 en 26 ficheros (`alerts.py` 6, `api.py` 3 …); los de ingesta están bien razonados (`otlp.py:513`, `api.py:97`), pero el patrón «registrar y seguir» (`api.py:104-107`, `ensure_project` a nivel debug) puede dejar proyectos sin registrar sin que nadie lo vea (ligado a C2).

**M5. Sin Dependabot/CODEOWNERS/protección de rama visible.** `.github/` sólo tiene `workflows/`. Dependencias con límite inferior (`>=`) y sin fichero de bloqueo para Python (`pyproject.toml`): una instalación nueva mañana puede romper la suite sin que cambie el código. `precios-litellm.yml` sí automatiza la caducidad de precios por PR (bien) pero eso significa que A4 podría resolverse sin humanos.

**M6. Mutaciones: la disciplina se declara más de lo que se ve.** `CLAUDE.md` exige «comprobar que muerde rompiendo el código a propósito»; en el repo no hay herramienta de mutación ni registro (sólo 7 menciones en `DECISIONS.md`). Se nota en los nombres (`test_dinero_desconocido.py`, `test_auditoria_p1/p2/p3`, `test_regla_*` por regla) y en comentarios de regresión («Regresión: `any(project_id)…`» en `test_clickhouse_store.py:130`), pero es una práctica manual no verificable. 63 `skip/xfail` en 45 ficheros: muchas son `skip` por «no hay ClickHouse» (`test_clickhouse_store.py:30`); en CI no saltan, en local sí — correcto, pero un desarrollador sin Docker ejecuta una suite que silenciosamente no prueba lo crítico (paridad, dinero en CH).

**M7. Hot spot de nube sin ensayo de caos.** Hay `test_ingesta_escala.py` y `test_carga.py`, pero nada que pruebe *ClickHouse cae a mitad de ingesta* (lote parcial, `marcar` tras `insert` en `clickhouse.py:~895`: si el insert entra y `preagregados.marcar` falla, el cliente reintenta, el segundo insert es idempotente — correcto — pero no hay prueba que lo fije). Tampoco hay prueba de reinicio de Postgres con el backend arriba (pool `psycopg` sin reconexión verificada).

#### BAJO

- **B1.** El «todo en español» se cumple (comentarios, commits, textos); mezcla con inglés sólo en nombres de API (`insert_spans`, `get_price_table`, `store`). Aceptable; la convención ya admite el SDK.
- **B2.** `.tmp-lap/` sin ignorar (`git status`); `.gitignore` incompleto.
- **B3.** `Dockerfile` instala `pip install` sin hash ni constraints y no usa multi-etapa; imagen `python:3.12-slim` sin fijar el digest.
- **B4.** `ci.yml` backend ejecuta la suite en serie (`pytest -q`, sin `-n`): entra en el timeout de 30 min sólo si va bien; no hay `pytest-timeout` ni reintento de servicios. Sin matriz Python 3.10 pese a `requires-python>=3.10` (el CI sólo prueba 3.12) ni Windows pese a ser donde se desarrolla.
- **B5.** Artefactos de Docker: el backend corre uvicorn con 1 proceso, sin `--workers` ni límite de memoria; Next y Caddy sin healthcheck.

### 4. Lo que falta

- Estrategia de backup/restauración probada (ClickHouse `BACKUP … TO Disk/S3` y `pg_dump`/WAL), con una prueba de restauración.
- Métricas (`/metrics` Prometheus) y alertas sobre el propio servicio; panel mínimo: ingesta/s, 503/min, p95 por ruta, atraso de preagregados, partes activas de ClickHouse, disco.
- Timeouts, límites por consulta y presupuesto de hilos para el cliente de ClickHouse; prueba de caos «CH caído».
- Runbook de operación (despliegue, migración, rollback, rotación de secretos, incidente de ClickHouse lleno).
- Un solo fichero «CONTRIBUTING» con el arranque verificado (Windows + Linux) y el tiempo esperado de la suite. Ahora el README (`README.md:490-501`) manda `docker compose up -d clickhouse postgres` + venv + `npm install`, y la suite exige además Node, Playwright/Chromium/WebKit (D-228) y los extras `[cloud,dev,typescript,saml]`: son 5+ cosas no escritas en un único sitio. Coste real de poner en marcha un entorno completo: ~30-60 min y ~10 GB; sin Docker, parte de las pruebas se saltan sin avisar.
- Política de versiones del SDK y del esquema de traza (compatibilidad hacia atrás para quien ya publicó 0.1).
- Registro de cobertura (hay `test_coverage.py`, pero es otra cosa: la cobertura *del producto*, no del código). Sin `pytest-cov`/umbral.

### 5. Propuestas

| # | Propuesta | Esfuerzo | Impacto |
|---|-----------|----------|---------|
| P1 | **Estabilizar la suite:** añadir `pytest-timeout` (`timeout=120` global, 600 para carga) y `timeout_method=thread`; una base ClickHouse *por proceso xdist* creada una vez en `conftest` (session) y vaciada con `TRUNCATE` en vez de `CREATE/DROP DATABASE … SYNC` por fichero; serializar las pruebas que usan `OPTIMIZE`/mutaciones con una marca `@pytest.mark.ch_pesada` (`-n 2` + `--dist loadgroup`). Que un cuelgue sea un fallo de 2 min, no 10 h. | M | Muy alto |
| P2 | **`test_pricing`:** sacar `test_la_tabla_no_esta_caducada` de la suite normal a un workflow semanal (`precios-litellm.yml` ya existe) o marcarlo `@pytest.mark.caducidad` y excluirlo del `pytest` por defecto, con CI que *abre una issue* cuando falla. Escribir D-201 de verdad. Suite en verde = señal limpia. | S | Alto |
| P3 | **Timeouts y límites en `ClickHouseStore`:** `connect_timeout=3`, `send_receive_timeout=30`, `settings={'max_execution_time': 30}` en lecturas; mantener 300 s sólo para `migrar_orden`/recálculos. Semáforo de ingestas concurrentes (p. ej. 16 de 40 hilos) que devuelve 503 + `Retry-After` en vez de agotar el pool. Prueba de caos con un puerto muerto/`SIGSTOP`. | S–M | Muy alto |
| P4 | **Backups:** servicio `backup` en compose con `pg_dump` diario + `clickhouse-backup` (o `BACKUP DATABASE … TO Disk`) a almacenamiento externo, retención 14 días; guía de restauración y una prueba mensual automatizada. | M | Crítico |
| P5 | **Observabilidad propia:** `prometheus-client` + `/metrics` (contador de spans ingeridos/rechazados, 503, histograma de latencia por ruta, atraso de preagregados), logs JSON con `request_id`, y 4 alertas (CH caído, 503 > 1 %, atraso > 10 min, disco > 80 %). Laplace puede incluso alimentarse de su propio OTLP. | M | Muy alto |
| P6 | **Compose de producción separado** (`docker-compose.prod.yml`): `restart: unless-stopped`, secretos por `.env`/Docker secrets, sin publicar 8000, Caddy con dominio y HTTPS, límites de memoria, healthchecks en web/entrada, `--workers 2`. | S–M | Alto |
| P7 | **Migraciones con versión:** tabla `schema_migrations` en Postgres y ClickHouse (id, hash, fecha), ejecución sólo si `auto_migrate` y con `pg_advisory_lock`/`CREATE TABLE … IF NOT EXISTS` bloqueado; `laplace migrate --dry-run`. Documentar que `migrar_orden` se ejecuta antes de aumentar tráfico y cuánto sitio reserva. | M | Alto |
| P8 | **Podar el proceso:** partir `DECISIONS.md` por año/bloque (`decisiones/2026-10.md`), mantener `docs/decisiones-indice.md` como única puerta, ordenar la numeración y cerrar los huecos (D-201, D-224–227); regla en `CLAUDE.md`: una decisión nueva sólo para cambios con criterio, no por cada arreglo. Hacer que la cabecera de `HOJA_DE_RUTA.md` se genere (fecha y última D-xxx) o desaparezca. | M | Medio |
| P9 | **Partir lo desmesurado** por ejes, no por tamaño: `clickhouse.py` → `clickhouse/{ingest,consultas_reglas,panel,cuentas}.py`; mismo corte en `sqlite.py`; y un contrato de paridad *declarativo* (una lista de consultas con el resultado esperado ejecutada contra los dos almacenes) en lugar de más pruebas sueltas. | L | Medio (a largo plazo, alto) |
| P10 | **Contribuir en 15 min:** `CONTRIBUTING.md` + `make dev`/`scripts/dev.ps1` que levante bases, cree venv e instale extras; matriz CI Python 3.10/3.12 y job Windows (sin servicios) para SQLite; `constraints.txt` o `uv.lock`; `dependabot.yml`; añadir `.tmp-lap/` a `.gitignore`. | S–M | Medio |
| P11 | **Mutaciones medibles:** `mutmut`/`cosmic-ray` mensual sobre `cifras.py`, `dinero.py`, `pricing/` y `auth.py` (el código con dinero y seguridad), con umbral, para convertir la práctica manual en evidencia. | M | Medio |

#### Orden sugerido
P3 → P1 → P2 (arreglan el dolor de las 10 h y la señal de CI) → P4 y P5 (condiciones de salida a la nube) → P6/P7 → P8/P10 → P9/P11.

---

## Auditoría 6 — Producto y honradez de las cifras

Fecha: 19 de octubre de 2026, sobre `master` en `47e65cd`. Sólo lectura. Lo comprobado de
verdad se hizo con SQLite: `laplace ui` en el puerto 8137 con un `LAPLACE_HOME` temporal,
`laplace demo --endpoint` y scripts en un directorio temporal, fuera del repo. No se ha
tocado ClickHouse ni Docker, y no se ha pasado la suite.

### 1. Veredicto

Laplace tiene una cultura de honradez poco común: hay guardas explícitas para el coste
desconocido, para proyectar con menos de un día, para los márgenes que se solapan y para
la cobertura, y casi todas están bien hechas y probadas. Pero **la pieza que lo distingue
del resto, el ahorro *verificado*, no verifica nada**: da un arreglo por bueno cuando el
hallazgo deja de aparecer, y eso pasa también cuando no aparece por otros motivos. Lo
he reproducido: el mismo agente, sin arreglar y lanzado como tirada de evaluación, sale
como «arreglado» y suma dinero a «Ya has dejado de pagar». Además, una configuración de
privacidad normal (`capture_content=False`) convierte un agente sano en un 98 % de
derroche; el modelo caro y el contexto fijo se reparten el mismo dinero; y la tarjeta
que más pesa en el héroe («el mismo trabajo costaría 10 veces menos») afirma una
equivalencia de calidad que nadie ha medido. Ninguna pieza del ciclo probar, arreglar
y verificar ha corrido contra un proveedor real, y el paquete no está en PyPI, así que
el primer comando de la documentación falla.

**Nota: 5/10.** Las guardas, el lenguaje y la arquitectura de las reglas valen un 7.
Lo bajan dos fallos confirmados que fabrican cifras en el camino principal (la
verificación y la privacidad), el doble conteo y el hecho de que todo el ciclo, salvo
detectar, sigue siendo una promesa probada contra servidores falsos.

### 2. Lo que está bien

- **El coste desconocido no es cero** en ningún sitio que haya mirado: `cost_unknown` y
  `cost_rate_assumed` viajan hasta la cifra y la convierten en «al menos»
  (`seguimiento.py:156`, `presupuesto.py:90`, `evals.py:322`). La tabla de LiteLLM entra
  por debajo y marcada como «no verificada» (`test_precios_litellm.py`).
- **No se proyecta con menos de un día**, y el gasto, el ahorro y el presupuesto salen de
  la misma base (`modelos.py:50`, `modelos.py:346-356`, `presupuesto.py:82`).
- **Acierto con Wilson, un mínimo de 10 casos y «no se distinguen» cuando los intervalos
  se solapan** (`evals.py:179-218`, `evals.py:404-412`). Está bien hecho.
- **La cobertura antes que el dinero**, con consecuencias y arreglos escritos para una
  persona.
- **El orden de las reglas para no contar dos veces** (`motor.py:257-370`) está pensado
  y documentado: repetición, bucle, truncada y JSON roto se descuentan antes del modelo
  caro y del contexto; prompts, lotes y caché van después, sobre lo que queda.
- **Las tiradas de evaluación quedan fuera de las reglas** (`sqlite.py:242-249`,
  `RULES_WHERE`).
- **La caché semántica y la parecida dicen «hasta» y no suman al total.** Es la lectura
  correcta.
- **Primer contacto:** la demo deja un Diagnóstico con frases claras, la proyección dice
  sobre cuántos días se hace y cada ficha enseña el cálculo.

### 3. Problemas, por gravedad

#### CRÍTICO 1 — El ahorro «verificado» da por arreglado lo que sólo ha dejado de verse

`seguimiento.py:91-99` decide con `detect()` sobre la ventana de después: si el hallazgo
con ese `id` no sale, el valor es 0 y el veredicto es «arreglado», con
`saved = por_antes × n_despues` (`seguimiento.py:146-157`). Esa ausencia no prueba que
se haya arreglado. El hallazgo también desaparece:

1. **Si después sólo hay tiradas de evaluación.** `summarize_window` cuenta todas las
   trazas (`WINDOW_WHERE`, `sqlite.py:240`), pero `detect` excluye las evaluaciones.
   **Reproducido** con `_agente()` de `test_sqlite_store.py`: dos días de tráfico con la
   repetición, se marca como arreglado y después se lanzan las mismas 6 ejecuciones,
   sin arreglar y etiquetadas `laplace-eval`. El resultado es
   `arreglado 6 0.002304 | En las 6 ejecuciones desde que lo marcaste no ha vuelto a
   aparecer… llevas 0,002304 US$ sin gastar`. El usuario lanza un replay para probar el
   arreglo, como le pide el producto, y ese replay se cuenta como ahorro verificado.
2. **Si después no se llega al umbral de la regla.** Con `MIN_EJECUCIONES = 5`, el contexto
   fijo exige 10 llamadas (`modelos.py:65`) y los lotes, 20 (`lotes.py:51`). Un paso que
   se llama una vez por ejecución no puede salir con 5 a 9 ejecuciones, y se da por
   «arreglado».
3. **Si cambia el `id`.** El modelo caro se identifica como `kind:paso:modelo`: si se
   cambia a un modelo *más caro* sin alternativa en la tabla, o el paso cambia de
   `step_key` al tocar el código, el hallazgo viejo desaparece y su ahorro se apunta
   entero.

Importa porque «Ya has dejado de pagar» es la cifra que se vende, y con la garantía de
la §6 de la hoja de ruta («10 veces lo que pagas o te devolvemos el mes») es la que se
cobraría.

#### CRÍTICO 2 — Con `capture_content=False`, un agente sano sale con un 98 % de derroche

`dedup_hash` es `sha256(tipo|nombre|modelo|entrada)` (`ingest/otlp.py:221-232`). Sin
contenido, la entrada está vacía (`integrations/_common.py:134-138`,
`otlp.py:601-604`), así que **todas las llamadas del mismo paso dentro de una traza
tienen el mismo hash** y la regla de repetición las da por copias.
**Reproducido** contra `laplace ui`: un agente que llama 4 veces al modelo por ejecución,
cada vez con una entrada distinta, 12 ejecuciones.

| | `capture_content=True` | `capture_content=False` |
|---|---|---|
| Hallazgos | modelo caro, 0,105 $ | **repetición «hasta 4 veces», 0,089 $** + modelo caro 0,025 $ + **caché semántica «responde lo mismo a la misma pregunta»** |
| Evitable | 0,105 de 0,117 $ | 0,114 de 0,117 $ (98 %) |
| Cobertura | «Entendemos el 100 %» | **«Entendemos el 100 %»** |

Apagar el contenido es lo primero que hace un equipo con datos de clientes, y
`docs/usuario/datos.md` lo ofrece. En ese caso el producto inventa el problema más fácil
de creer, y la cobertura dice que lo entiende todo.

#### ALTO 3 — El modelo caro y el contexto fijo cobran dos veces las lecturas de caché

En el contexto fijo, `ahorro += coste_lecturas` (`contexto_fijo.py:215-217`) suma el
**100 %** de lo que cuesta leer la caché, a la tarifa del modelo *actual*
(`contexto_fijo.py:134-145`). El encadenado con el modelo caro (`contexto_fijo.py:38-47`,
`:94`) sólo se aplica a la parte de cachear, no a esta. El modelo caro ya reclama la
diferencia de tarifa también sobre los tokens cacheados (`modelo_caro.py:216-228`).
**Reproducido** con las funciones de las dos reglas sobre un paso de `gpt-5.6-sol`
(1.000 llamadas, prefijo de 10.000 tokens, 8 M cacheados, 50 tokens de salida):

- coste real del paso: **12,20 $**;
- modelo caro (pasar a `terra`): 6,00 $; contexto fijo («leerla también se cobra»):
  3,20 $; **suma reclamada: 9,20 $**;
- lo máximo que se puede recuperar haciendo las dos cosas, con el prefijo a cero en
  `terra`: 6,00 + 1,60 = **7,60 $**. Se prometen 1,60 $ de más, un 17 %.

Además, dar por recuperable todo el coste de leer la caché supone mandar un prefijo de
cero tokens. La propia ficha dice que eso «baja mandando menos»: una cosa es que baje y
otra que desaparezca. Es un techo presentado como cifra, y suma al total del héroe sin
llevar «hasta».

#### ALTO 4 — «El mismo trabajo costaría 10 veces menos»: calidad afirmada sin medir

`textos/es.json:133-134` (`caro.lead`, `caro.resumen`) y el héroe («61,74 US$ que estás
tirando», `mensajes/es.ts:143`, con «Confianza alta», `es.ts:919-924`) presentan el
cambio de modelo como derroche cierto. La regla sólo mira que la salida típica sea de 60
tokens o menos (`modelos.py:63`, `motor.py:322-325`). En la demo, **48,7 de los 60 $
evitables son del modelo caro**, uno de ellos `verificar_politica`, que es justo el
tipo de paso (una decisión corta sobre algo difícil) en el que un modelo pequeño falla.
Una salida corta no quiere decir que la tarea sea fácil: un juez, un clasificador de
fraude o un reranker contestan en pocos tokens. El principio 2 del motor
(`insights/__init__.py:10-12`, «Nunca prometer calidad») se respeta en la ficha
(«Comprueba que la calidad aguanta»), pero no en la tarjeta ni en el total. «Confianza
alta» sólo mide la tarifa y la proporción, y lo dice (`conf.alta.texto`): no es la
confianza de que el ahorro exista.

#### ALTO 5 — El primer comando de la documentación falla

`docs/usuario/empezar.md` empieza con `pip install "laplace-trace[ui]"`, y
`https://pypi.org/pypi/laplace-trace/json` devuelve **404** (también `laplace-backend`).
Quien llegue desde un Show HN no puede instalarlo. Está en la §5 de la hoja de ruta como
tarea del usuario, pero mientras siga así el producto no existe para nadie de fuera.

#### MEDIO 6 — Las tiradas de evaluación se proyectan como gasto recurrente

`sqlite.py:242-245` lo decide a propósito («son gasto de verdad y se cuentan en el
gasto»), y que se cuenten está bien. Lo que está mal es que **se proyecten**: el
mensual del héroe (`motor.py:623`) y el cierre del presupuesto (`presupuesto.py:72-84`)
extrapolan `summarize_window`, que las incluye. En la demo, las 60 trazas de evaluación
son 8,56 $ gastados en un solo día (18 de octubre) y entran en los «136 US$ que te
costará este mes». Ejemplo con más peso: 10 $/día de tráfico y un replay de 200 $ el
día 3. El presupuesto proyecta (230/3) × 30 ≈ **2.300 $**, cuando el mes real anda
por los 500 $. Además diluyen el porcentaje evitable, porque cuentan en el denominador.

#### MEDIO 7 — El coste por caso de una tirada da por gratis las trazas que no encuentra

`side_for` (`evals.py:315-334`) suma sólo las trazas que hay en `costs`
(`costs_for_traces`, `sqlite.py:1800-1827`, que devuelve únicamente las que encuentra) y
divide entre **todos** los casos, sin marcar suelo. Con la retención por proyecto
(`datos.md`, «lo anterior se borra una vez al día»), o mirando la comparación antes de
que el SDK haya terminado de exportar, en una tirada de 10 casos con 4 trazas ausentes y
1 $ por caso sale un coste de **0,60 $ por caso**, presentado como exacto. Para los casos
limitados a spans sí se cuenta lo que falta (`evals.py:300-303`); para la traza entera, no.

#### MEDIO 8 — «La v2 cuesta un 26 % más» con 5 ejecuciones por lado y sin dispersión

`prompt_caro.py:58` (`MIN_TRACES = 5`) y `evals.py:53` (`MATERIAL_COST_CHANGE = 0.05`). El
coste por ejecución de un agente varía mucho (bucles, longitud de la conversación,
mezcla de preguntas), y con 5 ejecuciones una diferencia del 5 % es ruido. El propio
docstring lo reconoce: «con tres ejecuciones por lado, un porcentaje es la diferencia
entre dos anécdotas». Cinco no lo cambia mucho. El hallazgo pone dinero en el total y lo
proyecta. Es un porcentaje sin muestra suficiente, y sin intervalo que lo diga.

#### MEDIO 9 — La repetición no distingue el muestreo a propósito

`REPEATED_GROUPS_SQL` (`sqlite.py:303-340`) agrupa por la misma entrada y reclama el 100 %
de las copias. No mira la temperatura ni si las salidas son distintas. La
autoconsistencia, el *best-of-n* o el voto por mayoría (la misma pregunta 3 o 5 veces a
propósito) salen como «tu agente repite», con todo su coste como derroche. El bucle sí
mira las salidas (`MAX_SALIDAS_BUCLE`); la repetición tendría que hacer lo mismo.

#### MEDIO 10 — La primera vez es lenta

Medido en el portátil con SQLite: `laplace demo` tarda **70 s**, y el Diagnóstico de 30
días tarda **12,9 a 13,6 s** en `/api/overview` (6,3 s el de 7 días) con sólo **9.088
spans**. La primera pantalla se queda un buen rato en el esqueleto de carga. Para un
producto local que se vende con «en un minuto» es la primera impresión. Lo de fondo es
cosa de la auditoría de eficiencia; aquí importa que pasa en el primer minuto.

#### MEDIO 11 — El replay sólo sabe de dos proveedores y necesita el contenido

`replay.py:184-186` elige el proveedor por el nombre: lo que empieza por `claude` va a
Anthropic y todo lo demás a OpenAI. Gemini, Bedrock, Azure, Mistral o un modelo local
acaban en el cliente de OpenAI. Además, el replay reenvía los mensajes guardados: con
`capture_content=False` no hay nada que reenviar, así que los equipos que más cuidan la
privacidad no pueden «probar».

#### BAJO 12 — Dinero formateado fuera de `cifras.py`

`replay.py:182`, `cli.py:174` y `cli.py:189` usan `f"{x:.4f} $"`, con punto decimal,
mientras el producto escribe «0,0023 US$». El SDK no puede importar `cifras.py`, pero la
regla del repo dice dos sitios y aquí hay un tercero.

#### BAJO 13 — Documentación desfasada

- `docs/usuario/probar.md` dice que el replay sólo reenvía «llamadas sin herramientas»;
  D-205 ya las añadió.
- La tabla de `docs/usuario/diagnostico.md` no tiene la caché parecida ni el modelo lento.
- La tabla de estado del `README.md` deja la Fase 3 «pendiente» y no menciona Probar ni
  el bot.
- `caro.deteccion` dice «salida media», y el código usa la mediana cuando la hay
  (`motor.py:322-325`).

#### BAJO 14 — La demo se pone mala nota a sí misma

La cobertura de la demo sale como «malo» («Sólo el 28 % de tus llamadas con versión de
prompt») y aparece en el héroe. Para quien ve el producto por primera vez es un aviso
rojo sobre datos inventados por el propio producto.

### 4. El ciclo, paso a paso: dónde se sostiene y dónde es promesa

| Paso | Estado | Dónde se rompe o qué falta |
|---|---|---|
| **Detectar** | Funciona sobre trazas reales | Los fallos 2, 3, 4, 8 y 9. Los precios de OpenAI y Anthropic, transcritos a mano (56 modelos) |
| **Probar** (replay, `run_dataset`, juez) | Probado sólo con clientes reales y transporte **falso** (hoja de ruta §3) | Nunca contra una API real: falta una clave. Sólo OpenAI y Anthropic. Necesita el contenido. El juez pide `LAPLACE_EVALS_JUDGE_*`. El coste por caso puede salir bajo (fallo 7) |
| **Arreglar** (bot de PR) | Probado contra GitHub falso | La GitHub App sin registrar (§5); sin ella, un token por proyecto y el webhook a mano. Los arreglos de texto (D-221) los propone un modelo que nunca ha corrido de verdad. Sólo Python, TypeScript y JavaScript |
| **Verificar** | **Roto en el concepto** (fallo 1) | Mide si el hallazgo desaparece, no lo que cuesta el paso. Fusionar no es desplegar: sin `deployment_status` ni `service.version`, la frontera queda en la fusión |
| **Stripe / margen** | Contra una Stripe falsa | Falta una clave `rk_test_` |
| **Cifras contra la factura** | 4 pruebas vivas sin pasar | Falta una clave de proveedor |

Resumen: hoy sólo **detectar** existe de verdad. Los otros tres pasos son código bien
probado contra imitaciones, y el último mide otra cosa.

### 5. La primera vez (hecho de verdad)

1. `pip install "laplace-trace[ui]"`: **falla**, no está en PyPI. Desde el repo funciona
   con el `.venv`.
2. `laplace ui --port 8137 --no-browser` con `LAPLACE_HOME` temporal: arranca en menos de
   2 s. Muestra un aviso de desarrollador («hay otra copia construida que NO se está
   usando») que en una instalación desde PyPI no saldría.
3. `laplace demo --endpoint …`: **70 s**, «801 ejecuciones en 30 días». El proyecto
   tiene 861 trazas: las 60 que faltan son tiradas de evaluación, y la cifra no lo dice.
4. El Diagnóstico tarda unos **13 s** con 30 días. Después el valor se ve enseguida:
   «Puedes dejar de pagar hasta 61,74 US$ al mes de los 136 US$», 15 cosas que arreglar
   y la primera con su porqué.
5. `laplace.init()` + `llm_span`: unas líneas, y los datos están en la pantalla en
   segundos. **Lo que confunde:** `record_response()` no acepta texto suelto, pide
   `output_messages`. El error de tipo sale en el código del usuario.

**Tiempo hasta ver valor:** unos 2 minutos desde el repo con la demo. Desde fuera,
infinito, porque no se puede instalar.

### 6. Integraciones

**Probadas** (todas contra un proveedor falso):

- en Python, OpenAI (Chat, Responses, `parse`, streaming, `beta`) y Anthropic
  (`messages`, `stream`, `beta`), con los SDK de verdad y el transporte falso;
- en Node, OpenInference y OpenLLMetry con Anthropic, el AI SDK de Vercel 7,
  LangChain.js, LangGraph.js, el Agents SDK de OpenAI y Mastra
  (`scripts/integraciones_js`);
- la convivencia con otro instrumentador ya activo (`test_convivencia.py`).

**Sólo documentadas:** LangGraph, CrewAI y LlamaIndex en Python «vía OpenInference u
OpenLLMetry». No he encontrado pruebas con spans reales de esos frameworks.

**Lo que falta para el mercado de hoy:**

- **Gemini y Vertex, y Bedrock**, con integración propia y tarifa verificada: la tabla
  propia sólo tiene OpenAI y Anthropic, y lo demás llega de LiteLLM «sin verificar»;
- el **proxy y los callbacks de LiteLLM**, y gateways como OpenRouter o Portkey, que es
  por donde pasa mucho tráfico real;
- **Pydantic AI, Google ADK, el Claude Agent SDK, AWS Strands** y las llamadas a
  herramientas por **MCP**;
- el **SDK de TypeScript**, que espera el scope de npm;
- otros lenguajes para el bot de PR, empezando por Go y Java.

### 7. Lo que falta para que un equipo pague

- **Facturación y cuotas de Laplace:** no hay planes, ni medición de spans por
  organización, ni límites. `stripe_ingresos.py` trae los ingresos *del cliente*, no
  cobra el uso de Laplace. Sin esto, los precios de la §6 no se pueden aplicar.
- **Informes:** ni resumen semanal por correo o Slack, ni `laplace audit` con un informe
  compartible, que estaba propuesto como gancho de venta. Las alertas sólo avisan de
  umbrales.
- **Una API pública documentada:** hay 83 rutas en `/openapi.json`, pero ninguna guía, ni
  versionado, ni promesa de estabilidad. Para exportar hay CSV desde el navegador; no hay
  volcado programado a S3 o BigQuery.
- **Permisos:** hay cuatro roles y SAML/SCIM (probados contra un proveedor de identidad
  falso). No hay permisos por pantalla ni ocultación del contenido por rol, que importa
  si se guardan los mensajes.
- **Redacción de datos sensibles en la ingesta:** hoy es todo el contenido o nada, y
  apagarlo rompe la repetición, la caché y el replay (fallos 2 y 11).
- **Pruebas contra el mundo real:** las cuatro pruebas vivas, el replay, Stripe, SAML y el
  bot. Es un día de trabajo con claves de prueba, y es lo que convierte la promesa en
  producto.

### 8. Propuestas

| # | Propuesta | Esfuerzo | Impacto |
|---|---|---|---|
| 1 | **Verificar por el coste del paso, no por la ausencia del hallazgo.** Comparar el coste por ejecución del `step_key` (o del grupo de pasos del hallazgo) antes y después, sin tiradas de evaluación y con un intervalo; si el paso no tiene tráfico después, «pendiente», no «arreglado». Contar `n` también sin evaluaciones. Primero, la prueba roja del caso reproducido | M | Crítico: es la cifra que se vende |
| 2 | **Sin contenido, ni repetición ni caché semántica.** Si `input_messages` está vacío, `dedup_hash` vacío (la consulta ya filtra `!= ''`). Mejor aún: que el SDK mande un hash de la entrada sin mandar el contenido, para que la regla funcione con privacidad. Y una señal de cobertura «llamadas con entrada comparable» | S, o M con el hash | Crítico: quita un falso positivo masivo |
| 3 | **Encadenar las lecturas de caché con el modelo caro** (tarifar `coste_lecturas` con el modelo recomendado) y presentar «leerla también se cobra» como «hasta», fuera del total, o con un recorte que se pueda defender | S | Alto: total inflado |
| 4 | **Cambiar el lenguaje del modelo caro:** «costaría 10 veces menos *si responde igual de bien*». Que el héroe separe «comprobado» de «por probar», y que la confianza no sea «alta» mientras el ahorro del modelo caro no tenga un replay | S | Alto: honradez de la cifra principal |
| 5 | **Publicar `laplace-trace` en PyPI** (tarea del usuario, §5) | S | Alto: sin esto no hay usuarios |
| 6 | **No proyectar las tiradas de evaluación:** proyectar `gasto − evaluaciones` y enseñar estas aparte, como gasto puntual, en el héroe y en el presupuesto | S | Medio |
| 7 | **`side_for`: una traza ausente cuenta como coste desconocido** (`cost_is_floor`), igual que ya hace `coste_de_spans` | S | Medio |
| 8 | **Prompt más caro:** subir el mínimo y exigir que la diferencia salga del intervalo (*bootstrap* del coste por ejecución), o decir «no se distingue» | M | Medio |
| 9 | **Repetición:** excluir los grupos con salidas distintas y temperatura mayor que 0, o preguntar en lugar de poner dinero | S | Medio |
| 10 | **Primera vez:** que la demo cargue en menos de 10 s y el Diagnóstico de 30 días en menos de 2 s con 10.000 spans (precalcular al cargar la demo) | M | Medio: primera impresión |
| 11 | **Un día con claves de prueba:** las 4 pruebas vivas, un replay real, Stripe `rk_test_` y la GitHub App | S (del usuario) | Alto: pasa de promesa a hecho |
| 12 | **Facturación propia:** medir spans por organización, planes y límites | L | Necesario para cobrar |
| 13 | **Gemini, Bedrock y LiteLLM** con integración y tarifa propias; el replay con más proveedores (o a través de LiteLLM) | M | Mercado |
| 14 | **Un informe semanal** (correo o Slack) y `laplace audit` | M | Retención y venta |
| 15 | **Poner al día** `probar.md`, `diagnostico.md`, el README y `caro.deteccion`, y llevar el formato del dinero del SDK a un único sitio | S | Bajo |

### Anexo: cómo reproducir

- **Fallo 1:** con `_en` y `_agente` de `test_ajustes.py`, siembra dos días antes de
  marcar; después, `_agente()` con `tags += ["laplace-eval"]`; llama a
  `seguimiento.comprobar`. Sale `arreglado`, con `saved > 0`.
- **Fallo 2:** `laplace.init(..., capture_content=False)` y un `@observe(type="agent")`
  con 4 `llm_span` de entrada distinta por ejecución; `/api/overview` da repetición y
  caché semántica. Con `True`, ninguna de las dos.
- **Fallo 3:** `ModelUsage(model="gpt-5.6-sol", calls=1000, traces=100,
  input_tokens=10e6, cached_input_tokens=8e6, min_input_tokens=10000,
  output_tokens=50000, p50_output_tokens=50)`, pasado a `_expensive_model_finding` y
  `_fixed_context_finding`: 6,00 + 3,20 sobre 12,20 de coste.
