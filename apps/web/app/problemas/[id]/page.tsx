import Link from "next/link";
import { StaticTree } from "@/components/StaticTree";
import { BackendDown, NotFound } from "@/components/states";
import { backendReachable, getFinding, getTrace, listProjects, parseDays } from "@/lib/api";
import { duration, money, oneLine } from "@/lib/format";
import type { FindingDetail, Span } from "@/lib/types";

export const dynamic = "force-dynamic";

interface PageProps {
  params: { id: string };
  searchParams: { project?: string; days?: string };
}

/**
 * Ficha de un problema: qué pasa → por qué → (avanzado: cómo lo hemos detectado,
 * la traza y los atributos) → cómo arreglarlo → cuánto te ahorras.
 */
export default async function ProblemaPage({ params, searchParams }: PageProps) {
  if (!(await backendReachable())) {
    return <BackendDown apiUrl={process.env.LAPLACE_API_URL ?? "http://localhost:8000"} />;
  }

  const projects = await listProjects();
  const project = projects.find((p) => p.id === searchParams.project)?.id ?? projects[0]?.id ?? "";
  const days = parseDays(searchParams.days);
  const query = `?project=${encodeURIComponent(project)}&days=${days}`;

  const finding = await getFinding(decodeURIComponent(params.id), project, days);
  if (!finding) {
    return (
      <NotFound
        title="Ese problema ya no aparece"
        body="O lo has arreglado, o ha dejado de darse en el rango que estás mirando. Enhorabuena en cualquiera de los dos casos."
        back={`/${query}`}
      />
    );
  }

  const trace = finding.sample_trace_id ? await getTrace(finding.sample_trace_id) : null;
  const flaggedHash = finding.tech.find((t) => t.label === "laplace.dedup_hash")?.value;

  return (
    <main className="reading">
      <Link href={`/${query}`} className="back">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
          <path
            d="M8.5 3 L4.5 7 L8.5 11"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        Volver
      </Link>

      <Head finding={finding} days={days} />

      <section className="block">
        <h2>Qué está pasando</h2>
        <p>{finding.what_happens}</p>
        {finding.evidence.length > 0 && <Repetitions spans={finding.evidence} />}
      </section>

      <section className="block">
        <h2>Por qué pasa</h2>
        {finding.why.split("\n\n").map((paragraph, index) => (
          <p key={index}>{paragraph}</p>
        ))}
      </section>

      <section className="block pro">
        <h2>Cómo lo hemos detectado</h2>
        <Markup text={finding.detection_explanation} />
        <pre className="wrapless">{finding.detection_query}</pre>
        <p style={{ marginTop: 12 }}>
          Es la consulta que se ha ejecutado, no una versión de ejemplo. Los umbrales se
          podrán ajustar por proyecto cuando existan ajustes por proyecto.
        </p>
      </section>

      {trace && (
        <section className="block pro">
          <h2>La traza completa</h2>
          <p>
            Una ejecución real donde se ve el problema.{" "}
            <Link href={`/trazas/${trace.summary.trace_id}${query}`}>
              Ábrela para navegarla paso a paso →
            </Link>
          </p>
          <StaticTree trace={trace} highlight={flaggedHash} />
        </section>
      )}

      {finding.evidence.length > 0 && (
        <section className="block pro">
          <h2>Atributos del paso señalado</h2>
          <SpanAttributes span={finding.evidence[0]} />
        </section>
      )}

      <section className="block">
        <h2>Cómo arreglarlo</h2>
        <ol className="fix">
          {finding.fix_steps.map((step) => (
            <li key={step.title} className={step.advanced ? "pro" : undefined}>
              <b>{step.title}.</b> {step.body}
              {step.code && <pre>{step.code}</pre>}
            </li>
          ))}
        </ol>
      </section>

      <section className="block">
        <h2>Cuánto te ahorras</h2>
        {finding.costs_money ? (
          <p>
            Al ritmo de este rango, arreglarlo te deja de costar{" "}
            <strong>{money(finding.monthly_saving_usd, finding.currency)} al mes</strong>. En
            lo que llevas del rango ya se han ido{" "}
            <strong>{money(finding.window_waste_usd, finding.currency)}</strong>.
          </p>
        ) : (
          <p>
            Este problema no te cuesta dinero: los pasos repetidos no consumen tokens. Lo
            que te cuesta es espera, <strong>{duration(finding.window_waste_ms)}</strong> en
            el rango analizado. Arreglarlo hace que tu agente responda antes.
          </p>
        )}
        {finding.savings_calculation && (
          <p className="pro">
            <span style={{ color: "var(--ink-3)" }}>Cálculo: </span>
            {finding.savings_calculation}
          </p>
        )}
        <p className="disclaimer">{finding.savings_note}</p>
      </section>

      <div className="actions">
        {/* Recarga completa a propósito: recalcula el diagnóstico desde cero. */}
        <a href={`/problemas/${encodeURIComponent(finding.id)}${query}`} className="btn primary">
          Ya lo he arreglado, vuelve a medir
        </a>
        <Link href={`/trazas${query}&q=${encodeURIComponent(nameOf(finding))}`} className="btn">
          Ver las trazas afectadas
        </Link>
        {trace && (
          <a
            className="btn pro"
            href={`/api/export/${trace.summary.trace_id}`}
            download={`${trace.summary.trace_id}.json`}
          >
            Exportar traza en JSON
          </a>
        )}
      </div>
    </main>
  );
}

/** El nombre del paso implicado, para buscar sus trazas. */
function nameOf(finding: FindingDetail): string {
  return finding.tech.find((t) => t.label === "paso")?.value ?? "";
}

function Head({ finding, days }: { finding: FindingDetail; days: number }) {
  return (
    <div className="d-head">
      <h1>{finding.title}</h1>
      <div className="d-sub pro">
        {finding.tech.map((t) => `${t.label} ${t.value}`).join(" · ")}
      </div>
      <div className="d-cost">
        {finding.costs_money ? (
          <>
            <div>
              <b className="num">{money(finding.monthly_saving_usd, finding.currency)}</b>
              <span>al mes si no cambia nada</span>
            </div>
            <div>
              <b className="num">{money(finding.window_waste_usd, finding.currency)}</b>
              <span>ya gastados en {days === 1 ? "24 horas" : `${days} días`}</span>
            </div>
          </>
        ) : (
          <div>
            <b className="num neutral">{duration(finding.window_waste_ms)}</b>
            <span>de espera evitable en {days === 1 ? "24 horas" : `${days} días`}</span>
          </div>
        )}
        {finding.scope_label && (
          <div>
            <b className="num neutral">{finding.scope_label}</b>
            <span>afectadas</span>
          </div>
        )}
      </div>
    </div>
  );
}

/** Las ocurrencias repetidas, con las idénticas resaltadas. */
function Repetitions({ spans }: { spans: Span[] }) {
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
          …{rest} {rest === 1 ? "llamada idéntica más" : "llamadas idénticas más"}, todas con la
          misma respuesta.
        </div>
      )}
    </div>
  );
}

function SpanAttributes({ span }: { span: Span }) {
  const rows: [string, string][] = [
    ["trace_id", span.trace_id],
    ["span_id", span.span_id],
    ["laplace.span.type", span.type],
    ["laplace.dedup_hash", span.dedup_hash],
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
function Markup({ text }: { text: string }) {
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
