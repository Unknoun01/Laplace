# Laplace

Laplace encuentra el dinero que tu agente de IA tira, te dice cómo arreglarlo y
demuestra cuánto has dejado de pagar.

Mira las trazas de tu agente —cada llamada al modelo, cada herramienta, cada paso—,
calcula lo que cuesta cada una con la tarifa de su proveedor y busca cinco formas
concretas de gastar de más. Cada problema que encuentra pasa por cuatro pasos:

1. **Detectar.** El Diagnóstico dice qué paso gasta de más, cuánto y por qué.
2. **Probar.** Antes de cambiar nada, se prueba el arreglo sobre las llamadas reales de
   ese paso: ¿el modelo barato responde igual?
3. **Arreglar.** Lo cambias en tu código y lo marcas como arreglado.
4. **Verificar.** Laplace compara lo que costaba cada ejecución antes con lo que cuesta
   ahora, y suma lo que ya has dejado de pagar.

## Las páginas

- [Empezar](empezar.md): en local en un minuto, o la instalación completa.
- [Instrumentar tu agente](instrumentar.md): Python, TypeScript y cualquier otra cosa
  que hable OpenTelemetry.
- [Leer el Diagnóstico](diagnostico.md): las cinco reglas y qué quiere decir cada cifra.
- [Probar un arreglo](probar.md): conjuntos de casos, `laplace replay` y el juez.
- [Alertas y presupuesto](alertas.md).
- [Tus datos](datos.md): qué se guarda, cuánto tiempo y cómo se borra.

## Lo que no hace

Laplace **no ejecuta tu agente** ni guarda tus claves de proveedor: todo lo que llama
al modelo corre en tu máquina. Y no afirma lo que no sabe: un modelo sin tarifa no
cuesta cero, sino «no lo sabemos», y con pocos datos no hay porcentaje.
