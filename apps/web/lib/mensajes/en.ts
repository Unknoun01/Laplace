import type { Mensajes } from "./es";

export const en: Mensajes = {
  "tiempo.dias_one": "{n} day",
  "tiempo.dias_other": "{n} days",
  "tiempo.horas_one": "{n} hour",
  "tiempo.horas_other": "{n} hours",
  "tiempo.minutos_one": "{n} minute",
  "tiempo.minutos_other": "{n} minutes",
  "tiempo.menos_de_un_minuto": "less than a minute",
  "ventana.dias_one": "the last day",
  "ventana.dias_other": "the last {n} days",
  "ventana.horas_one": "the last hour",
  "ventana.horas_other": "the last {n} hours",
  "ventana.minutos_one": "the last minute",
  "ventana.minutos_other": "the last {n} minutes",
  "relativo.ahora": "just now",
  "relativo.min": "{n} min ago",
  "relativo.h": "{n} h ago",
  "relativo.d": "{n} d ago",

  "nav.diagnostico": "Diagnosis",
  "nav.trazas": "Traces",
  "nav.panel": "Dashboard",
  "nav.evaluaciones": "Evaluations",
  "nav.prompts": "Prompts",
  "nav.ajustes": "Settings",

  "barra.proyecto": "Project",
  "barra.sin_proyectos": "No projects",
  "barra.rango": "Time range",
  "barra.idioma": "Language",
  "barra.avanzado": "Advanced",
  "barra.avanzado_ayuda": "Shows the technical layer: queries, attributes, identifiers",
  "barra.tu_cuenta": "Your account: {email}",
  "barra.organizacion": "Organization and account",
  "barra.salir": "Sign out",
  "rango.1": "24 hours",
  "rango.7": "7 days",
  "rango.30": "30 days",

  "estado.sin_backend.titulo": "We can't reach the backend",
  "estado.sin_backend.texto":
    "It isn't responding. If you're running locally, start it with {local}; for the full install, with {docker}.",
  "estado.reintentar": "Retry",
  "estado.sin_proyecto.titulo": "No projects yet",
  "estado.sin_proyecto.texto":
    "As soon as your agent sends its first run, it will show up here. Instrumenting it takes one line:",
  "estado.primer_proyecto":
    "On this install the agent needs a key to send traces. {enlace}: the snippet above appears there with the key filled in.",
  "estado.primer_proyecto.enlace": "Create one in Organization",
  "estado.demo.texto":
    "Just want to see it working? Load some sample traces (made-up data, in a separate project called “demo”) and see what it finds.",
  "estado.demo.cargando": "Loading…",
  "estado.demo.boton": "Load sample data",
  "estado.demo.terminal": "From a terminal: {comando}.",
  "estado.demo.error": "couldn't load them",
  "estado.sin_trazas.titulo": "Waiting for the first run of “{proyecto}”",
  "estado.sin_trazas.texto":
    "The project exists but no traces have arrived in the range you're looking at. Try widening the range in the bar above, or run your agent with Laplace enabled.",
  "estado.nada.titulo_con_aviso": "We found nothing to fix, but we couldn't look at everything",
  "estado.nada.titulo": "You're not wasting money right now",
  "estado.nada.texto":
    "We looked for repeated calls, steps using a more expensive model than they need, and context resent for no reason. There's none of that in this range.",
  "estado.clave.titulo": "This install requires a key",
  "estado.clave.sin_clave":
    "If you don't have one, whoever administers this organization creates it, in Organization → Keys.",
  "estado.clave.guardar": "Save and sign in",
  "estado.clave.con_cuenta": "Sign in with your account",
  "estado.clave.cookie":
    "It's checked and stored in a cookie only this Laplace can see: neither the page nor any script can read it, and it never goes in the URL, because whatever goes in the URL ends up in the logs of every proxy it passes through.",
  "estado.ajeno.titulo": "Your key doesn't give access to this project",
  "estado.ajeno.texto": "This key is for another project on this install.",
  "estado.ajeno.otra": "Use another key",
  "estado.volver": "Back",
};
