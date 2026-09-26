/**
 * Catálogo de origen (D-147). Define las claves: los otros idiomas se tipan contra él.
 *
 * Los huecos van entre llaves. Los que se rellenan con un elemento —un enlace, un
 * `<code>`— se rellenan con `tr()`; los demás, con `t()`. Los plurales son dos claves,
 * `_one` y `_other`, y se piden con `tn()`.
 */
export const es = {
  // Duraciones en palabras. Espejo de `span_label` y `window_label` del backend.
  "tiempo.dias_one": "{n} día",
  "tiempo.dias_other": "{n} días",
  "tiempo.horas_one": "{n} hora",
  "tiempo.horas_other": "{n} horas",
  "tiempo.minutos_one": "{n} minuto",
  "tiempo.minutos_other": "{n} minutos",
  "tiempo.menos_de_un_minuto": "menos de un minuto",
  "ventana.dias_one": "el último día",
  "ventana.dias_other": "los últimos {n} días",
  "ventana.horas_one": "la última hora",
  "ventana.horas_other": "las últimas {n} horas",
  "ventana.minutos_one": "el último minuto",
  "ventana.minutos_other": "los últimos {n} minutos",
  "relativo.ahora": "hace un momento",
  "relativo.min": "hace {n} min",
  "relativo.h": "hace {n} h",
  "relativo.d": "hace {n} d",

  "nav.diagnostico": "Diagnóstico",
  "nav.trazas": "Trazas",
  "nav.panel": "Panel",
  "nav.evaluaciones": "Evaluaciones",
  "nav.prompts": "Prompts",
  "nav.ajustes": "Ajustes",

  "barra.proyecto": "Proyecto",
  "barra.sin_proyectos": "Sin proyectos",
  "barra.rango": "Rango temporal",
  "barra.idioma": "Idioma",
  "barra.avanzado": "Avanzado",
  "barra.avanzado_ayuda": "Enseña la capa técnica: consultas, atributos, identificadores",
  "barra.tu_cuenta": "Tu cuenta: {email}",
  "barra.organizacion": "Organización y cuenta",
  "barra.salir": "Salir",
  "rango.1": "24 horas",
  "rango.7": "7 días",
  "rango.30": "30 días",

  "estado.sin_backend.titulo": "No podemos conectar con el backend",
  "estado.sin_backend.texto":
    "No responde. Si estás en local, arráncalo con {local}; si es la instalación completa, con {docker}.",
  "estado.reintentar": "Reintentar",
  "estado.sin_proyecto.titulo": "Todavía no hay ningún proyecto",
  "estado.sin_proyecto.texto":
    "En cuanto tu agente envíe su primera ejecución, aparecerá aquí. Instrumentarlo es una línea:",
  "estado.primer_proyecto":
    "En esta instalación el agente necesita una clave para enviar trazas. {enlace}: ahí mismo sale el fragmento de arriba con la clave puesta.",
  "estado.primer_proyecto.enlace": "Créala en Organización",
  "estado.demo.texto":
    "¿Sólo quieres verlo funcionando? Carga unas trazas de ejemplo —datos inventados, en un proyecto aparte llamado «demo»— y mira qué detecta.",
  "estado.demo.cargando": "Cargando…",
  "estado.demo.boton": "Cargar datos de ejemplo",
  "estado.demo.terminal": "Desde una terminal: {comando}.",
  "estado.demo.error": "no se han podido cargar",
  "estado.sin_trazas.titulo": "Esperando la primera ejecución de «{proyecto}»",
  "estado.sin_trazas.texto":
    "El proyecto existe pero no ha llegado ninguna traza en el rango que estás mirando. Prueba a ampliar el rango en la barra de arriba, o lanza tu agente con Laplace activado.",
  "estado.nada.titulo_con_aviso":
    "No hemos encontrado nada que arreglar, pero no hemos podido mirarlo todo",
  "estado.nada.titulo": "No estás tirando dinero ahora mismo",
  "estado.nada.texto":
    "Hemos buscado llamadas repetidas, pasos que usan un modelo más caro del que necesitan y contexto que se reenvía sin hacer falta. No hay nada de eso en este rango.",
  "estado.clave.titulo": "Esta instalación pide una clave",
  "estado.clave.sin_clave":
    "Si no tienes una, la crea quien administra esta organización, en Organización → Claves.",
  "estado.clave.guardar": "Guardar y entrar",
  "estado.clave.con_cuenta": "Entrar con tu cuenta",
  "estado.clave.cookie":
    "Se comprueba y se guarda en una cookie que sólo ve este Laplace: ni la página ni ningún script pueden leerla, y nunca va en la URL —lo que va en la URL acaba en los logs de cualquier proxy por el que pase—.",
  "estado.ajeno.titulo": "Tu clave no da acceso a este proyecto",
  "estado.ajeno.texto": "Esta clave sirve para otro proyecto de esta instalación.",
  "estado.ajeno.otra": "Usar otra clave",
  "estado.volver": "Volver",
} as const;

export type Clave = keyof typeof es;
export type Mensajes = Record<Clave, string>;
