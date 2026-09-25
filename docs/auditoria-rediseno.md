# Auditoría del rediseño (D-132 + D-133), 2026-09-25

Rama `claude/funny-sagan-iwnppp`. Revisado en marcha: Docker (`localhost:3000`, con
cuenta) y modo local (`127.0.0.1:8100`, proyecto `demo` con trazas de ejemplo).
Se escribe a medida que se revisa, así que el final puede estar a medias.

Severidad: **A** lo nota un usuario y le confunde o le esconde algo · **B** feo o
incómodo · **C** detalle.

## Hallazgos

### A1 · «Esperando la primera ejecución» cuando sí hay trazas
`demo-viajes` tiene 154 spans (7 sept) pero con el rango en 7 días el Diagnóstico dice
que no ha llegado ninguna. Es lo primero que se ve tras entrar y hace pensar que la
instalación no funciona. Debería distinguir «nunca ha llegado nada» de «nada en este
rango»: decir cuándo fue la última y ofrecer ampliar el rango con un clic.

Es barato: `/api/projects` ya devuelve `last_seen` por proyecto
(`apps/backend/laplace_backend/api.py:289`). Basta con pasarlo a `NoTracesYet`
(`apps/web/components/states.tsx:172`) y, si existe, cambiar el título a «No hay
ejecuciones en los últimos 7 días» + «La última llegó el 7 sept» + un botón que ponga el
rango que la incluye. El texto actual ya lo sospecha («prueba a ampliar el rango»), pero
bajo un titular que dice lo contrario.

### A2 · Trazas en móvil: el coste queda fuera de pantalla
A 375 px la tabla hace scroll horizontal y de la columna de coste sólo asoma el «$». La
lista se ordena por coste, así que es justo la cifra que importa.

### A3 · Tema claro: el ámbar de los costes no pasa AA — ARREGLADO (42 %, mínimo 4,72)
`--amber` en claro es `color-mix(var(--accent-3) 58%, #3a2400)`, unos `rgb(147,117,32)`.
Sobre el cristal rosa da **3,4–3,95:1** (hace falta 4,5 para texto normal). Es el color de
las cifras que más importan:
- Diagnóstico: la cifra de cada problema («$13,05 al mes», 20 px) y el número de orden
  (13 px).
- Trazas: la columna de coste (16 filas, 3,63) y la etiqueta «bucle» (11 px, **3,40**, la
  peor).
- Prompts: los costes (13 px) y el contador «3 juegos de instrucciones».

D-133 dice «en claro los acentos se oscurecen como texto para no bajar de AA», pero el
ámbar se quedó corto. Arreglo: subir la mezcla hacia `#3a2400` hasta ~75 % o fijar un
`#7a5a00`, y medir otra vez.

### B0 · Tema claro: «Borrar el proyecto» a 4,17:1 — ARREGLADO (`--rose` #a52d3c)
En Ajustes, `--rose` (`#b93344`) sobre el cristal no llega a 4,5. Existe `--rose-ink`
(`#9a2a37`), que sí pasaría: usar ese para el texto.

### B2 · Página de problema: el texto se contradice
En «modelo caro», el paso «Comprueba que la calidad aguanta» dice «Cuando exista la capa de
evaluación podrás hacerlo desde aquí; hoy toca a mano», y dos bloques más abajo la misma
página ofrece «Crear el conjunto … con estas ejecuciones» para Evaluaciones. Es texto que
se quedó viejo en `apps/backend/laplace_backend/insights/modelo_caro.py:373`. No es del
rediseño, pero salta a la vista en cuanto se lee la página.

### B3 · Página de problema: «En todas las ejecuciones» con aspecto de cifra
`apps/web/app/problema/tecnico.tsx:49` pinta `scope_label` con `className="num"`. Con
D-133 `.num` es Plex Mono al tamaño de las cifras grandes, así que una frase («En todas
las ejecuciones», «9 de cada 10 ejecuciones») sale en monoespaciada enorme al lado de
«$13,05» y «$1,16», y se parte en dos líneas. Debería ir en la fuente de texto o partirse
en cifra («10/10») + etiqueta.

### C3 · Comillas dentro de comillas en los títulos
«Usas el modelo caro…: «responder — «Responde usando exclusivamente e…»»»: tres niveles
de «» y un recorte a media palabra. Mejor: el paso en negrita o en código, sin comillas
anidadas.

### B1 · Navegación recortada hacia 1000 px
A 1024 px `nav.nav` mide 474 px y su contenido 504: «Ajustes» queda bajo el difuminado
aunque en la cabecera sobra sitio. El `mask-image` es fijo, así que al llegar al final
del scroll el último enlace sigue difuminado.

### C1 · «$14 , 64» en móvil
Confirmado: la cifra del héroe (`div.big.num`) es IBM Plex Mono, con `letter-spacing`
negativo (−3,5 px a 63 px). En una monoespaciada la coma ocupa una celda entera, así que
queda un hueco a cada lado; en escritorio se nota menos porque la cifra es más grande y el
tracking la compensa. Arreglo: Inter con `font-variant-numeric: tabular-nums` para la cifra
grande, y la mono para las columnas, que es donde sirve alinear.

### C2 · La barra «/» de la cabecera, 3,95:1 en claro — ARREGLADO (`--muted` #7a6262)
Es decorativa, así que no incumple AA, pero es el único texto del tema claro que baja
de 4,5.

## Orden propuesto
1. **A3** (ámbar en claro): una línea de CSS, y afecta a las cifras de toda la app.
2. **A1** (vacío engañoso): sólo frontend, el dato ya existe.
3. **B0 + C2**: cambiar `--rose` por `--rose-ink` en el botón de borrar y dar algo más de
   peso a la «/».
4. **B3 + C1**: tipografía de las cifras (frases fuera de `.num`, cifra grande en Inter).
5. **B2**: quitar la frase vieja de `modelo_caro.py`.
6. **A2 + B1**: coste visible en móvil y navegación a ~1000 px.

## Sin revisar
- Las páginas con sesión de la versión Docker (Organización, invitaciones, claves): no
  escribo contraseñas en el navegador. La API de sesión sí se probó con curl (entrar y
  `/api/auth/me` funcionan).
- Flujos de escritura: anotar, crear conjuntos, lanzar evaluaciones, marcar como
  arreglado.
- Navegadores distintos de Chromium (en Safari el `backdrop-filter` y `color-mix` se
  comportan algo distinto).

## Comprobado y bien
- Las 6 pestañas cargan sin errores de consola.
- Contraste medido en las 6 pestañas componiendo los fondos translúcidos sobre
  `--bg-gradient-mid` (aproximación: ignora el blur y los degradados de fondo). Tema
  oscuro: todo pasa AA salvo la «/» decorativa (3,03). Tema claro: todo pasa salvo lo que
  recogen A3 y B0.
- Móvil: la página no se sale por los lados (scrollWidth = 375).
- Rendimiento del cristal: `backdrop-filter` está en dos reglas; en Trazas, con 16 filas,
  sólo lo llevan 2 elementos (la cabecera y «Ver en vivo»). No se aplica por fila, así que
  no hay riesgo de que se arrastre con tablas largas.
- El foco del teclado se ve (`:focus-visible` con contorno `--iris`) y hay reglas
  `prefers-reduced-motion`.
- Detalle de traza: carga bien, sin errores, con cabecera de cifras, lista de problemas y
  árbol de ejecución.
- Cabeceras de seguridad iguales en Docker (Caddy + Next) y en local (uvicorn):
  `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`.
- Pantalla de entrada (Docker): el botón «Entrar» (`#0b1020` sobre el degradado violeta
  y cian) ronda 6:1.
