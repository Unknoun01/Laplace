import type { Mensajes } from "./es";

export const pt: Mensajes = {
  "tiempo.dias_one": "{n} dia",
  "tiempo.dias_other": "{n} dias",
  "tiempo.horas_one": "{n} hora",
  "tiempo.horas_other": "{n} horas",
  "tiempo.minutos_one": "{n} minuto",
  "tiempo.minutos_other": "{n} minutos",
  "tiempo.menos_de_un_minuto": "menos de um minuto",
  "ventana.dias_one": "o último dia",
  "ventana.dias_other": "os últimos {n} dias",
  "ventana.horas_one": "a última hora",
  "ventana.horas_other": "as últimas {n} horas",
  "ventana.minutos_one": "o último minuto",
  "ventana.minutos_other": "os últimos {n} minutos",
  "relativo.ahora": "agora mesmo",
  "relativo.min": "há {n} min",
  "relativo.h": "há {n} h",
  "relativo.d": "há {n} d",

  "nav.diagnostico": "Diagnóstico",
  "nav.trazas": "Traces",
  "nav.panel": "Painel",
  "nav.evaluaciones": "Avaliações",
  "nav.prompts": "Prompts",
  "nav.ajustes": "Configurações",

  "barra.proyecto": "Projeto",
  "barra.sin_proyectos": "Sem projetos",
  "barra.rango": "Intervalo de tempo",
  "barra.idioma": "Idioma",
  "barra.avanzado": "Avançado",
  "barra.avanzado_ayuda": "Mostra a camada técnica: consultas, atributos, identificadores",
  "barra.tu_cuenta": "Sua conta: {email}",
  "barra.organizacion": "Organização e conta",
  "barra.salir": "Sair",
  "rango.1": "24 horas",
  "rango.7": "7 dias",
  "rango.30": "30 dias",

  "estado.sin_backend.titulo": "Não conseguimos conectar ao backend",
  "estado.sin_backend.texto":
    "Ele não responde. Se você está rodando localmente, inicie com {local}; na instalação completa, com {docker}.",
  "estado.reintentar": "Tentar de novo",
  "estado.sin_proyecto.titulo": "Ainda não há nenhum projeto",
  "estado.sin_proyecto.texto":
    "Assim que seu agente enviar a primeira execução, ela aparecerá aqui. Instrumentá-lo é uma linha:",
  "estado.primer_proyecto":
    "Nesta instalação o agente precisa de uma chave para enviar traces. {enlace}: ali mesmo aparece o trecho acima com a chave preenchida.",
  "estado.primer_proyecto.enlace": "Crie-a em Organização",
  "estado.demo.texto":
    "Só quer ver funcionando? Carregue alguns traces de exemplo (dados inventados, em um projeto separado chamado “demo”) e veja o que ele detecta.",
  "estado.demo.cargando": "Carregando…",
  "estado.demo.boton": "Carregar dados de exemplo",
  "estado.demo.terminal": "Em um terminal: {comando}.",
  "estado.demo.error": "não foi possível carregá-los",
  "estado.sin_trazas.titulo": "Aguardando a primeira execução de “{proyecto}”",
  "estado.sin_trazas.texto":
    "O projeto existe, mas nenhum trace chegou no intervalo que você está vendo. Tente ampliar o intervalo na barra acima ou execute seu agente com o Laplace ativado.",
  "estado.nada.titulo_con_aviso": "Não encontramos nada para corrigir, mas não conseguimos olhar tudo",
  "estado.nada.titulo": "Você não está desperdiçando dinheiro agora",
  "estado.nada.texto":
    "Procuramos chamadas repetidas, etapas que usam um modelo mais caro do que precisam e contexto reenviado sem necessidade. Não há nada disso neste intervalo.",
  "estado.clave.titulo": "Esta instalação exige uma chave",
  "estado.clave.sin_clave":
    "Se você não tem uma, quem administra esta organização a cria, em Organização → Chaves.",
  "estado.clave.guardar": "Salvar e entrar",
  "estado.clave.con_cuenta": "Entrar com sua conta",
  "estado.clave.cookie":
    "Ela é verificada e guardada em um cookie que só este Laplace vê: nem a página nem nenhum script podem lê-la, e ela nunca vai na URL, porque o que vai na URL acaba nos logs de qualquer proxy por onde passe.",
  "estado.ajeno.titulo": "Sua chave não dá acesso a este projeto",
  "estado.ajeno.texto": "Esta chave serve para outro projeto desta instalação.",
  "estado.ajeno.otra": "Usar outra chave",
  "estado.volver": "Voltar",
};
