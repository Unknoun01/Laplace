"use client";

import { money, number, spanLabel } from "@/lib/format";
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
        <strong>Casi todo lo que gastas está señalado aquí abajo:</strong>{" "}
        {money(evitable, overview.currency)} de {money(total, overview.currency)} (
        {Math.round((evitable / total) * 100)} %).
      </p>
      {/* La explicación sigue aquí, entera, pero plegada: delante tapaba la lista de
          problemas, que es lo que se ha venido a ver (D-124). */}
      <details className="porque">
        <summary>¿Por qué no lo presentamos como ahorro?</summary>
        <p>
          Porque «puedes dejar de pagar tu agente» casi nunca es lo que significa. Lo que
          suele haber detrás es un agente pequeño o recién estrenado donde dos o tres pasos
          son prácticamente toda la factura, y arreglarlos cambia esos pasos, no el agente
          entero. Mira el desglose de cada problema antes de contar con esta cifra.
        </p>
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
export function Señales({ cobertura }: { cobertura: Coverage }) {
  return (
    <ul className="senales">
      {cobertura.signals.map((s) => (
        <li key={s.key} className={s.level}>
          <div className="stop">
            <span>{s.label}</span>
            <b>
              {s.level === "no-aplica"
                ? "no lo usas"
                : s.value === null
                  ? "—"
                  : /* espacio duro: «100 %» no puede partirse en dos líneas */
                    `${(s.value * 100).toFixed(0)} %`}
            </b>
          </div>
          <div className="sbar" role="img" aria-label={`${(s.value ?? 0) * 100} por ciento`}>
            <i style={{ width: `${(s.value ?? 0) * 100}%` }} />
          </div>
          <small>
            {s.value === null
              ? s.unavailable
              : `${number(s.counted)} de ${number(s.total)} llamadas`}
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
export function Caveats({
  overview,
  ventana,
  casiTodo,
}: {
  overview: Overview;
  ventana: string;
  casiTodo: boolean;
}) {
  // Cada aviso es una línea que se lee siempre y un porqué que se despliega. Antes
  // eran párrafos enteros, y en un móvil la primera pantalla no enseñaba ni un solo
  // problema: el rigor tapaba lo que el rigor protege (D-124).
  const avisos: { linea: React.ReactNode; porque: React.ReactNode }[] = [];

  if (overview.unknown_cost_spans > 0) {
    avisos.push({
      linea: (
        <>
          <strong>Este total está incompleto:</strong> {number(overview.unknown_cost_spans)}{" "}
          pasos sin tarifa ({overview.models_without_price.join(", ")}).
        </>
      ),
      porque: (
        <>
          Su modelo no está en nuestra tabla de precios, así que no sabemos cuánto cuestan
          y no se suman. Puedes ponerle precio en Ajustes, y se recalcula lo ya guardado.
        </>
      ),
    });
  }
  if (overview.assumed_rate_spans > 0) {
    avisos.push({
      linea: (
        <>
          <strong>Es un suelo:</strong> en {number(overview.assumed_rate_spans)}{" "}
          {overview.assumed_rate_spans === 1 ? "paso" : "pasos"} hemos cobrado la tarifa
          estándar.
        </>
      ),
      porque: (
        <>
          Contexto largo, residencia de datos o modo rápido son metros aparte que la
          respuesta del proveedor no siempre revela. El coste real puede ser algo mayor,
          nunca menor. En cada paso, en modo avanzado, se dice cuál es la duda.
        </>
      ),
    });
  }
  if (overview.projected) {
    avisos.push({
      linea: (
        <>
          Proyección desde <strong>{ventana}</strong> de datos.
        </>
      ),
      porque: (
        <>
          Es lo que llevas enviando, no el rango que pide el selector. Gasto y ahorro salen
          de la misma base: si cambia una, cambia la otra.
        </>
      ),
    });
  } else {
    avisos.push({
      linea: (
        <>
          <strong>Dinero ya gastado, sin proyectar.</strong> La previsión mensual aparece
          con {spanLabel(overview.min_days_for_projection)} de datos.
        </>
      ),
      porque: (
        <>
          Con {ventana} de datos, multiplicar para llegar a 30 días da una cifra que no se
          sostiene: un pico de diez minutos se convertiría en cientos de dólares.
        </>
      ),
    });
  }
  // Cuando el evitable es casi todo, esto ya lo dice el bloque de arriba. Decirlo dos
  // veces en la misma pantalla no lo hace más creíble.
  if (overview.savings_needs_caution && !casiTodo) {
    avisos.push({
      linea: (
        <>
          El ahorro estimado es el{" "}
          <strong>{Math.round(overview.avoidable_ratio * 100)} %</strong> de lo que gastas.
        </>
      ),
      porque: (
        <>
          Es mucho: suele pasar en agentes pequeños o recién estrenados, donde unos pocos
          pasos dominan la factura. Antes de darlo por bueno, mira el desglose de cada
          problema.
        </>
      ),
    });
  }

  if (avisos.length === 0) return null;
  return (
    <div className="caveats compactos">
      {avisos.map((aviso, index) => (
        <details key={index} className="porque">
          <summary>{aviso.linea}</summary>
          <p>{aviso.porque}</p>
        </details>
      ))}
    </div>
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
      <b>No podemos calcular el dinero</b>
      <span>{motivo}</span>
    </div>
  );
}
