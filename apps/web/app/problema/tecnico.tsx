"use client";

import { duration, money, oneLine, spanLabel } from "@/lib/format";
import { Euros } from "@/lib/moneda";
import type { FindingDetail, Span } from "@/lib/types";

/**
 * La cabecera con las cifras.
 *
 * El periodo es el **observado**, no el que pide el selector: con dos horas de datos
 * en un rango de siete días, decir «ya gastados en 7 días» es falso, y era lo que
 * decía antes (D-073).
 */
export function Head({ finding }: { finding: FindingDetail }) {
  const ventana = spanLabel(finding.observed_days);
  return (
    <div className="d-head">
      <h1>{finding.title}</h1>
      <div className="d-sub pro">
        {finding.tech.map((t) => `${t.label} ${t.value}`).join(" · ")}
      </div>
      <div className="d-cost">
        {finding.costs_money ? (
          <>
            {finding.monthly_saving_usd !== null && (
              <div>
                <b className="num">{money(finding.monthly_saving_usd, finding.currency)}</b>
                <span>al mes si no cambia nada</span>
              </div>
            )}
            <div>
              <b className="num">
                {finding.cost_is_floor ? "≥ " : ""}
                {money(finding.window_waste_usd, finding.currency)}
              </b>
              <span>
                ya gastados en {ventana} <Euros usd={finding.window_waste_usd} />
              </span>
            </div>
          </>
        ) : (
          <div>
            <b className="num neutral">{duration(finding.window_waste_ms)}</b>
            <span>de espera evitable en {ventana}</span>
          </div>
        )}
        {finding.scope_label && (
          <div>
            <b className="neutral frase">{finding.scope_label}</b>
            <span>afectadas</span>
          </div>
        )}
      </div>
    </div>
  );
}

/** Las ocurrencias repetidas, con las idénticas resaltadas. */
export function Repetitions({ spans, bucle }: { spans: Span[]; bucle: boolean }) {
  const shown = spans.slice(0, 3);
  const rest = spans.length - shown.length;

  return (
    <div className="reps">
      {shown.map((span, index) => (
        <div key={span.span_id} className="rep same">
          <span className="n">{index + 1}</span>
          <span className="what">
            {span.name} <em>{oneLine(span.tool?.arguments ?? span.input ?? span.llm?.input_messages)}</em>
          </span>
          <span className="c">{money(span.llm?.cost.total_usd ?? 0)}</span>
        </div>
      ))}
      {rest > 0 && (
        <div className="reps-note">
          {/* En un bucle las entradas NO son idénticas —ésa es la diferencia con la
              repetición—, así que llamarlas así contradecía la frase de arriba. */}
          …{rest}{" "}
          {bucle
            ? rest === 1
              ? "vuelta más"
              : "vueltas más"
            : rest === 1
              ? "llamada idéntica más"
              : "llamadas idénticas más"}
          , todas con la misma respuesta.
        </div>
      )}
    </div>
  );
}

export function SpanAttributes({ span }: { span: Span }) {
  const rows: [string, string][] = [
    ["trace_id", span.trace_id],
    ["span_id", span.span_id],
    ["laplace.span.type", span.type],
    ["laplace.dedup_hash", span.dedup_hash],
    ["laplace.step.key", span.step_key],
    ["laplace.step.label", span.step_label],
    ["duration_ms", span.duration_ms.toFixed(0)],
  ];
  if (span.tool?.name) rows.push(["gen_ai.tool.name", span.tool.name]);
  if (span.llm?.request_model) rows.push(["gen_ai.request.model", span.llm.request_model]);
  if (span.llm) {
    rows.push(["gen_ai.usage.input_tokens", String(span.llm.usage.input_tokens)]);
    rows.push(["gen_ai.usage.output_tokens", String(span.llm.usage.output_tokens)]);
    // La caché es la mitad del cálculo de coste: sin verla, la cifra no se audita.
    rows.push([
      "laplace.usage.cached_input_tokens",
      String(span.llm.usage.cached_input_tokens),
    ]);
    rows.push(["laplace.usage.cache_write_tokens", String(span.llm.usage.cache_write_tokens)]);
    if (span.llm.cost.rate) rows.push(["tarifa aplicada", span.llm.cost.rate]);
    if (span.llm.cost.rate_assumed) rows.push(["tarifa asumida", span.llm.cost.rate_note]);
  }
  if (span.session_id) rows.push(["laplace.session.id", span.session_id]);
  if (span.user_id) rows.push(["laplace.user.id", span.user_id]);

  return (
    <dl className="kv">
      {rows.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Negritas de markdown y `código`, que es todo lo que usan los textos de las reglas. */
export function Markup({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <p>
      {parts.map((part, index) => {
        if (part.startsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
        if (part.startsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
        return <span key={index}>{part}</span>;
      })}
    </p>
  );
}
