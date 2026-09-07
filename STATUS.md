# Estado de Laplace

Última actualización: 7 de septiembre de 2026.

## Dónde está el producto

Laplace observa agentes de IA y dice cuánto cuestan y cuánto pueden dejar de costar. Ese
ciclo —observar, diagnosticar, ahorrar— está **cerrado y funcionando de punta a punta**
por los dos caminos: la instalación de nube con Docker y el modo local de un solo
proceso. Un agente instrumentado con una línea de Python emite trazas por OTLP, la
ingesta les calcula el coste por tramos de tokens, tres reglas deterministas encuentran
el derroche, y la interfaz lo cuenta en dinero con el cálculo detrás. La Fase 1 queda
completa con `laplace ui`, y la Fase 2 con las reglas y el panel de ahorro; lo que falta
son las alertas, el diagnóstico automático con modelo y las pestañas de evaluación y
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
  mismo dinero.
- **98 pruebas.** Las que necesitan ClickHouse se saltan solas; las del modo local corren
  siempre.

## Qué queda

En el orden acordado:

1. **Alertas a Slack** (cierra la Fase 2). Disparo cuando una regla supera su umbral,
   configurable por proyecto, con el hallazgo, el dinero y un enlace directo. Umbral y
   silenciado configurables: una alerta ruidosa se ignora, y luego se ignoran todas.
2. **Pestaña Panel.** Sólo tiene sentido con las dos ideas que la justifican: coste por
   unidad de trabajo (por ejecución, no totales) y atribución de picos con causas reales.
   Sin eso es un Grafana peor. Modo en vivo por polling; WebSockets, más adelante.
3. **Pestaña Evaluaciones.** Anotación humana, LLM-as-judge claramente separado en la
   interfaz y en los datos, conjuntos de casos a partir de tráfico real, y la comparación
   A vs B de acierto y coste a la vez, que es lo valioso.
4. **Pestaña Prompts.** Versiones con diff, cuál está en producción, rollback en un clic,
   y las métricas reales de cada versión pegadas a ella.

Los huecos de las fases 3 y 4 ya existen en el contrato, en el esquema de Postgres y en
la API: devuelven `null` y listas vacías, así que cuando lleguen no hay migración.

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

**La proyección mensual sobre pocas horas.** Con una hora de datos, el mes proyectado se
multiplica por 720 y sale una cifra grande y poco creíble. Está avisado en pantalla y el
suelo es de una hora, pero es lo primero que va a chirriar a un usuario nuevo: conviene
mirar si el aviso basta o si hay que dejar de proyectar por debajo de cierto umbral.

**El modo local puede derivar del de nube.** El test de paridad lo protege, pero sólo
compara lo que compara: si añades un campo al almacén, añádelo a los dos y comprueba que
sigue pasando. Y `laplace ui` necesita que la interfaz esté construida
(`python scripts/build_ui.py` antes de publicar el paquete); si falta, la API arranca y
dice cómo construirla en lugar de servir un 404 mudo.

**El agente sin instrumentar manda.** `examples/agente_sin_instrumentar.py` es lo que
sale de leer diez líneas del README con prisa, y es el que decide si un cambio en las
reglas sirve. Un cambio que sólo se ve bien en `agente_ejemplo.py`, que está
cuidadosamente instrumentado, no está verificado.
