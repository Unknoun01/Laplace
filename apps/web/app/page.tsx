import Link from "next/link";
import { BigMoney, FindingCard, GapBar, Readout } from "@/components/pieces";
import { BackendDown, NoProject, NoTracesYet, NothingToFix } from "@/components/states";
import { backendReachable, getOverview, listProjects, parseDays } from "@/lib/api";
import { duration, money, number, percent, tokens } from "@/lib/format";

export const dynamic = "force-dynamic";

interface PageProps {
  searchParams: { project?: string; days?: string };
}

/**
 * Inicio: el dinero primero.
 *
 * Lo primero que ve cualquiera es cuánto cuesta su agente y cuánto puede dejar de
 * costar. Todo lo demás cuelga de ahí. La capa técnica (latencia, tokens, spans) sólo
 * aparece en modo avanzado.
 */
export default async function DiagnosticoPage({ searchParams }: PageProps) {
  if (!(await backendReachable())) {
    return <BackendDown apiUrl={process.env.LAPLACE_API_URL ?? "http://localhost:8000"} />;
  }

  const projects = await listProjects();
  if (projects.length === 0) return <NoProject />;

  const project = projects.find((p) => p.id === searchParams.project)?.id ?? projects[0].id;
  const days = parseDays(searchParams.days);
  const overview = await getOverview(project, days);
  const query = `?project=${encodeURIComponent(project)}&days=${days}`;

  if (overview.spans === 0) return <NoTracesYet project={project} />;

  const ahorra = overview.monthly_avoidable_usd > 0;

  return (
    <main className="reading">
      <section className="hero">
        <h1>
          Tu agente «{project}», al ritmo de {days === 1 ? "las últimas 24 horas" : `estos ${days} días`}
        </h1>

        <div className="pair">
          <BigMoney
            amount={overview.monthly_cost_usd}
            currency={overview.currency}
            label="te costará este mes"
          />
          {ahorra && (
            <>
              <div className="arrow" aria-hidden>
                →
              </div>
              <BigMoney
                amount={overview.monthly_necessary_usd}
                currency={overview.currency}
                label="si arreglas lo de abajo"
                good
              />
            </>
          )}
        </div>

        {ahorra && (
          <GapBar
            necessary={overview.monthly_necessary_usd}
            avoidable={overview.monthly_avoidable_usd}
            currency={overview.currency}
          />
        )}

        <Readout
          items={[
            [duration(overview.p95_duration_ms), "Latencia p95"],
            [percent(overview.error_rate), "Ejecuciones con error"],
            [tokens(overview.input_tokens + overview.output_tokens), "Tokens"],
            [number(overview.traces), "Trazas"],
            [number(overview.spans), "Pasos"],
            [money(overview.cost_per_trace_usd, overview.currency), "Coste por ejecución"],
          ]}
        />
      </section>

      {overview.findings.length === 0 ? (
        <NothingToFix>
          <p style={{ marginTop: 14 }}>
            Llevas {money(overview.window_cost_usd, overview.currency)} gastados en{" "}
            {number(overview.traces)} ejecuciones.
          </p>
          <div className="actions">
            <Link href={`/trazas${query}`} className="btn">
              Ver todas las trazas
            </Link>
          </div>
        </NothingToFix>
      ) : (
        <section className="sec">
          <h2>
            {overview.findings.length === 1
              ? "Una cosa que arreglar"
              : `${overview.findings.length} cosas que arreglar`}
          </h2>
          <p className="lead">De la que más dinero te devuelve a la que menos.</p>
          {overview.findings.map((finding) => (
            <FindingCard
              key={finding.id}
              finding={finding}
              href={`/problemas/${encodeURIComponent(finding.id)}${query}`}
            />
          ))}
          <p className="disclaimer">
            Los importes son una estimación a partir de lo que ha pasado en el rango que
            estás mirando, proyectado a 30 días. Si tu tráfico cambia, cambian. El coste
            está en {overview.currency} porque es la moneda en la que facturan los
            proveedores.
          </p>
        </section>
      )}
    </main>
  );
}
