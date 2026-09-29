"use client";

import { money, number, porcentaje, spanLabel } from "@/lib/format";
import { tr } from "@/lib/i18n";
import { t, tn } from "@/lib/textos";
import type { Coverage, Overview } from "@/lib/types";

/**
 * Lo que va donde iría la barra de reparto cuando el reparto no reparte nada.
 *
 * Dice la cifra —no la esconde, está medida— y dice por qué no se la presentamos como
 * una promesa. Es la misma regla que el resto del producto: una cifra se enseña con lo
 * que haga falta para leerla bien.
 */
export function CasiTodoEvitable({
  overview,
  total,
  evitable,
}: {
  overview: Overview;
  total: number;
  evitable: number;
}) {
  return (
    <div className="casitodo">
      <p>
        <strong>{t("avisos.casi_todo")}</strong>{" "}
        {t("avisos.casi_todo.cifras", {
          evitable: money(evitable, overview.currency),
          total: money(total, overview.currency),
          pct: porcentaje(evitable / total),
        })}
      </p>
      {/* La explicación sigue aquí, entera, pero plegada: delante tapaba la lista de
          problemas, que es lo que se ha venido a ver (D-124). */}
      <details className="porque">
        <summary>{t("avisos.casi_todo.porque")}</summary>
        <p>{t("avisos.casi_todo.texto")}</p>
      </details>
    </div>
  );
}

/**
 * Por encima de esta parte del gasto señalada como evitable, el héroe deja de
 * enseñar la pareja «X → Y» y la barra de reparto.
 *
 * No es un umbral cosmético. Con el 93 % evitable, la barra queda con un lado
 * invisible y la segunda cifra grande dice, en letra de 54 px, que puedes dejar de
 * pagar casi todo tu agente. Nadie se lo cree, y con razón: lo que suele haber detrás
 * es un agente pequeño donde dos o tres pasos son la factura entera. El aviso de
 * `savings_needs_caution` ya salta antes (al 60 %), pero va debajo de la cifra, y aquí
 * pasa lo mismo que con el «$0» de D-073: el número se lee antes que el aviso.
 */
export const CASI_TODO_EVITABLE = 0.9;

/**
 * El bloque de cobertura, cuando hay algo que avisar.
 *
 * Va antes del dinero y no en Avanzado. La regla del producto es que una cifra se
 * enseña con lo que haga falta para leerla bien, y aquí lo que hace falta es saber
 * sobre cuánto del agente está calculada.
 */
export function CoberturaBloque({ cobertura }: { cobertura: Coverage }) {
  return (
    <section className={`cobertura ${cobertura.level}`}>
      <h2>{cobertura.headline}</h2>
      <p>{cobertura.detail}</p>
      <Señales cobertura={cobertura} />
    </section>
  );
}

/** Una línea cuando todo va bien. Se puede desplegar para ver las cuatro señales. */
export function CoberturaLinea({ cobertura }: { cobertura: Coverage }) {
  if (cobertura.level === "sin-base" && cobertura.llm_calls === 0) return null;
  return (
    <details className="cobertura-linea">
      <summary>
        <i className={`punto ${cobertura.level}`} aria-hidden /> {cobertura.headline}
      </summary>
      <p>{cobertura.detail}</p>
      <Señales cobertura={cobertura} />
    </details>
  );
}

/**
 * Las cuatro señales, cada una con su barra.
 *
 * Sin porcentaje cuando no hay llamadas suficientes: se dice por qué, igual que en
 * todas las demás proporciones del producto (D-087).
 */
function Señales({ cobertura }: { cobertura: Coverage }) {
  return (
    <ul className="senales">
      {cobertura.signals.map((s) => (
        <li key={s.key} className={s.level}>
          <div className="stop">
            <span>{s.label}</span>
            <b>
              {s.level === "no-aplica"
                ? t("avisos.no_lo_usas")
                : s.value === null
                  ? "—"
                  : /* espacio duro: «100 %» no puede partirse en dos líneas */
                    porcentaje(s.value)}
            </b>
          </div>
          <div className="sbar" role="img" aria-label={t("avisos.pct_aria", { n: Math.round((s.value ?? 0) * 100) })}>
            <i style={{ width: `${(s.value ?? 0) * 100}%` }} />
          </div>
          <small>
            {s.value === null
              ? s.unavailable
              : t("avisos.de_llamadas", { n: number(s.counted), total: number(s.total) })}
          </small>
          {/* El «qué hacer» sólo cuando hace falta: si la señal va bien, es ruido. */}
          {(s.level === "malo" || s.level === "flojo") && <small className="fix">{s.fix}</small>}
        </li>
      ))}
    </ul>
  );
}

/**
 * Avisos que acompañan a la cifra grande.
 *
 * Una cifra de ahorro que nadie se cree no vende nada: cuando el evitable es casi todo
 * el gasto, o cuando la proyección sale de unas horas de datos, se dice aquí mismo en
 * lugar de presentarlo como una promesa.
 */
type Aviso = { linea: React.ReactNode; porque: React.ReactNode; peso: "baja" | "media" | "nota" };

/**
 * Las salvedades de la cifra del héroe, como datos. Cada una tiene una línea, su
 * porqué y cuánto pesa en la confianza: «baja» si falta dinero por contar, «media» si
 * lo contado puede quedarse corto o es desproporcionado, «nota» si sólo explica de
 * dónde sale (una proyección no es una duda: es cómo se calcula).
 */
function avisosDe(overview: Overview, ventana: string, casiTodo: boolean): Aviso[] {
  // Cada aviso es una línea que se lee siempre y un porqué que se despliega. Antes
  // eran párrafos enteros, y en un móvil la primera pantalla no enseñaba ni un solo
  // problema: el rigor tapaba lo que el rigor protege (D-124).
  const avisos: Aviso[] = [];

  if (overview.unknown_cost_spans > 0) {
    avisos.push({
      linea: (
        <>
          <strong>{t("avisos.incompleto")}</strong>{" "}
          {t("avisos.incompleto.linea", {
            n: number(overview.unknown_cost_spans),
            modelos: overview.models_without_price.join(", "),
          })}
        </>
      ),
      porque: (
<>{t("avisos.incompleto.porque")}</>
      ),
      peso: "baja",
    });
  }
  if (overview.assumed_rate_spans > 0) {
    avisos.push({
      linea: (
        <>
          <strong>{t("avisos.suelo")}</strong>{" "}
          {tn("avisos.suelo.linea", overview.assumed_rate_spans, {
            n: number(overview.assumed_rate_spans),
          })}
        </>
      ),
      porque: (
<>{t("avisos.suelo.porque")}</>
      ),
      peso: "media",
    });
  }
  if (overview.projected) {
    avisos.push({
      linea: (
<>{tr("avisos.proyeccion", { ventana: <strong>{ventana}</strong> })}</>
      ),
      porque: (
<>{t("avisos.proyeccion.porque")}</>
      ),
      peso: "nota",
    });
  } else {
    avisos.push({
      linea: (
        <>
          <strong>{t("avisos.sin_proyectar")}</strong>{" "}
          {t("avisos.sin_proyectar.linea", {
            tiempo: spanLabel(overview.min_days_for_projection),
          })}
        </>
      ),
      porque: (
<>{t("avisos.sin_proyectar.porque", { ventana })}</>
      ),
      peso: "nota",
    });
  }
  // Cuando el evitable es casi todo, esto ya lo dice el bloque de arriba. Decirlo dos
  // veces en la misma pantalla no lo hace más creíble.
  if (overview.savings_needs_caution && !casiTodo) {
    avisos.push({
      linea: (
        <>
          {tr("avisos.mucho", {
            pct: <strong>{porcentaje(overview.avoidable_ratio)}</strong>,
          })}
        </>
      ),
      porque: (
<>{t("avisos.mucho.porque")}</>
      ),
      peso: "media",
    });
  }
  return avisos;
}

/**
 * Las salvedades como un distintivo de confianza y no como un párrafo (D-151).
 *
 * Una línea por salvedad debajo de la cifra seguían siendo tres o cuatro líneas entre
 * el dinero y la lista de problemas. El distintivo dice de un vistazo cuánto fiarse
 * —alta, media, baja— y se despliega para dar las salvedades enteras, cada una con su
 * porqué: nada se esconde, sólo se pliega. El nivel lo pone la peor salvedad, y la
 * cobertura mala (que ya va en su bloque, arriba) también lo baja.
 */
export function Confianza({
  overview,
  ventana,
  casiTodo,
  coberturaMala,
}: {
  overview: Overview;
  ventana: string;
  casiTodo: boolean;
  coberturaMala: boolean;
}) {
  const avisos = avisosDe(overview, ventana, casiTodo);
  const nivel =
    coberturaMala || avisos.some((a) => a.peso === "baja")
      ? "baja"
      : avisos.some((a) => a.peso === "media")
        ? "media"
        : "alta";
  return (
    <details className={`confianza ${nivel}`}>
      <summary>
        <i className="punto" aria-hidden /> {t(`conf.${nivel}`)}
        {avisos.length > 0 && (
          <span className="cuantas"> · {tn("conf.notas", avisos.length, { n: avisos.length })}</span>
        )}
      </summary>
      <div className="confianza-cuerpo">
        <p className="confianza-nivel">{t(`conf.${nivel}.texto`)}</p>
        {avisos.map((aviso, index) => (
          <details key={index} className="porque">
            <summary>{aviso.linea}</summary>
            <p>{aviso.porque}</p>
          </details>
        ))}
      </div>
    </details>
  );
}

/**
 * Lo que va donde iría el dinero cuando no se puede calcular.
 *
 * No es un caso raro: le pasa a cualquiera que use modelos locales —un estudiante
 * probando con Ollama— y a todo el mundo el día que sale un modelo nuevo y todavía no
 * está en la tabla de precios. Lo que se enseña es el motivo, y el detalle de abajo
 * sigue con tokens, trazas y latencia, que son datos medidos.
 */
export function SinDinero({ motivo }: { motivo: string }) {
  return (
    <div className="nomoney">
      <b>{t("avisos.sin_dinero")}</b>
      <span>{motivo}</span>
    </div>
  );
}
