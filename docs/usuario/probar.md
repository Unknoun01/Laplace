# Probar un arreglo

El ahorro está medido; que el arreglo responda igual de bien, no. Eso se prueba antes
de desplegar, sobre las llamadas reales del paso.

## Guardar las ejecuciones del paso

En la ficha de un problema, o en **Probar → Arreglos por probar**, un botón guarda las
ejecuciones reales de ese paso como un **conjunto de casos**.

## El modelo barato, sin escribir código

```bash
laplace replay "<conjunto>" --modelo gpt-5.6-luna --tope 1 --proyecto mi-agente
```

- Reenvía las **mismas llamadas** del paso, con los mismos mensajes, al modelo barato.
- Corre en tu máquina y con tu clave (`OPENAI_API_KEY` o `ANTHROPIC_API_KEY`).
- **No se pasa del tope**: antes de cada llamada suma lo peor que puede costar y, si se
  pasaría, no la hace.
- **Pide permiso** antes de gastar, diciendo cuántas llamadas y cuánto como mucho.
- Sólo reenvía lo que no tiene efectos: llamadas sin herramientas, con los mensajes en
  texto.

Deja dos tiradas para comparar en **Probar**: la original, con lo que costaron esas
llamadas, y la del modelo nuevo.

## Con tu agente entero

Para otros arreglos (la versión anterior de un prompt, un cambio de código), corre tu
agente sobre los casos:

```python
laplace.run_dataset("<conjunto>", mi_agente, variant="arreglado")
```

## El juez

Si lo enciendes (`LAPLACE_EVALS_JUDGE_*`), un modelo juzga cada respuesta: si hace lo
que se pedía y si se inventa algo. Lo que cuesta juzgar se mide y se enseña aparte. Su
veredicto y el de una persona nunca se mezclan, y con pocos casos no hay porcentaje.
