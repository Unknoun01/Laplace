import type { Mensajes } from "./es";

// Les espaces avant « : ; ? ! » sont insécables (U+00A0), comme le veut la typographie.
export const fr: Mensajes = {
  "tiempo.dias_one": "{n} jour",
  "tiempo.dias_other": "{n} jours",
  "tiempo.horas_one": "{n} heure",
  "tiempo.horas_other": "{n} heures",
  "tiempo.minutos_one": "{n} minute",
  "tiempo.minutos_other": "{n} minutes",
  "tiempo.menos_de_un_minuto": "moins d’une minute",
  "ventana.dias_one": "le dernier jour",
  "ventana.dias_other": "les {n} derniers jours",
  "ventana.horas_one": "la dernière heure",
  "ventana.horas_other": "les {n} dernières heures",
  "ventana.minutos_one": "la dernière minute",
  "ventana.minutos_other": "les {n} dernières minutes",
  "relativo.ahora": "à l’instant",
  "relativo.min": "il y a {n} min",
  "relativo.h": "il y a {n} h",
  "relativo.d": "il y a {n} j",

  "nav.diagnostico": "Diagnostic",
  "nav.trazas": "Traces",
  "nav.panel": "Tableau de bord",
  "nav.evaluaciones": "Évaluations",
  "nav.prompts": "Prompts",
  "nav.ajustes": "Réglages",

  "barra.proyecto": "Projet",
  "barra.sin_proyectos": "Aucun projet",
  "barra.rango": "Période",
  "barra.idioma": "Langue",
  "barra.avanzado": "Avancé",
  "barra.avanzado_ayuda": "Affiche la couche technique : requêtes, attributs, identifiants",
  "barra.tu_cuenta": "Votre compte : {email}",
  "barra.organizacion": "Organisation et compte",
  "barra.salir": "Se déconnecter",
  "rango.1": "24 heures",
  "rango.7": "7 jours",
  "rango.30": "30 jours",

  "estado.sin_backend.titulo": "Impossible de joindre le backend",
  "estado.sin_backend.texto":
    "Il ne répond pas. En local, lancez-le avec {local} ; pour l’installation complète, avec {docker}.",
  "estado.reintentar": "Réessayer",
  "estado.sin_proyecto.titulo": "Aucun projet pour l’instant",
  "estado.sin_proyecto.texto":
    "Dès que votre agent enverra sa première exécution, elle apparaîtra ici. L’instrumenter tient en une ligne :",
  "estado.primer_proyecto":
    "Sur cette installation, l’agent a besoin d’une clé pour envoyer des traces. {enlace} : l’extrait ci-dessus y apparaît avec la clé déjà remplie.",
  "estado.primer_proyecto.enlace": "Créez-la dans Organisation",
  "estado.demo.texto":
    "Vous voulez juste le voir fonctionner ? Chargez des traces d’exemple (des données inventées, dans un projet à part nommé « demo ») et regardez ce qu’il détecte.",
  "estado.demo.cargando": "Chargement…",
  "estado.demo.boton": "Charger des données d’exemple",
  "estado.demo.terminal": "Depuis un terminal : {comando}.",
  "estado.demo.error": "impossible de les charger",
  "estado.sin_trazas.titulo": "En attente de la première exécution de « {proyecto} »",
  "estado.sin_trazas.texto":
    "Le projet existe mais aucune trace n’est arrivée sur la période affichée. Élargissez la période dans la barre du haut, ou lancez votre agent avec Laplace activé.",
  "estado.nada.titulo_con_aviso":
    "Nous n’avons rien trouvé à corriger, mais nous n’avons pas pu tout examiner",
  "estado.nada.titulo": "Vous ne gaspillez pas d’argent en ce moment",
  "estado.nada.texto":
    "Nous avons cherché des appels répétés, des étapes qui utilisent un modèle plus cher que nécessaire et du contexte renvoyé sans raison. Il n’y a rien de tout cela sur cette période.",
  "estado.clave.titulo": "Cette installation demande une clé",
  "estado.clave.sin_clave":
    "Si vous n’en avez pas, la personne qui administre cette organisation la crée, dans Organisation → Clés.",
  "estado.clave.guardar": "Enregistrer et entrer",
  "estado.clave.con_cuenta": "Se connecter avec votre compte",
  "estado.clave.cookie":
    "Elle est vérifiée puis stockée dans un cookie que seul ce Laplace voit : ni la page ni aucun script ne peuvent la lire, et elle ne passe jamais dans l’URL, car ce qui passe dans l’URL finit dans les journaux de chaque proxy traversé.",
  "estado.ajeno.titulo": "Votre clé ne donne pas accès à ce projet",
  "estado.ajeno.texto": "Cette clé sert à un autre projet de cette installation.",
  "estado.ajeno.otra": "Utiliser une autre clé",
  "estado.volver": "Retour",
};
