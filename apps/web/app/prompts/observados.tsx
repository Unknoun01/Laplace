"use client";

import Link from "next/link";
import { money, number, timestamp } from "@/lib/format";
import type { ObservedStep, PromptsView } from "@/lib/types";
import { tr } from "@/lib/i18n";
import { t } from "@/lib/textos";

export function SinAdoptar({ project }: { project: string }) {
  return (
    <section className="sec">
      <h2>{t("pr.sin_adoptar")}</h2>
      <p className="lead">{tr("pr.sin_adoptar.1", { blame: <code>git blame</code> })}</p>
      <p className="lead">
        {tr("pr.sin_adoptar.2", { nada: <strong>{t("pr.sin_adoptar.nada")}</strong> })}
      </p>
      <pre>{`import laplace
laplace.init(project="${project}")

sistema = laplace.get_prompt("resumen", fallback=SISTEMA_DEL_CODIGO)
respuesta = cliente.messages.create(
    model="claude-haiku-4-5",
    system=sistema.render(idioma="es"),
    messages=[{"role": "user", "content": pregunta}],
)`}</pre>
      <p className="disclaimer">{t("pr.sin_adoptar.3")}</p>
    </section>
  );
}

// ---------------------------------------------------------------------------------
// Lo inferido de las trazas
// ---------------------------------------------------------------------------------

export function Observados({
  vista,
  query,
  managed,
}: {
  vista: PromptsView;
  query: string;
  managed: boolean;
}) {
  if (vista.observed.length === 0) {
    return (
      <section className="sec">
        <h2>{t("pr.obs.titulo")}</h2>
        <p className="lead">{vista.observed_unavailable || t("pr.obs.nada")}</p>
      </section>
    );
  }

  return (
    <section className="sec">
      <h2>{t("pr.obs.titulo")}</h2>
      <p className="lead">{managed ? t("pr.obs.gestion") : t("pr.obs.sin_gestion")}</p>
      {vista.observed.map((paso) => (
        <PasoObservado key={paso.label} paso={paso} />
      ))}
      <p className="disclaimer">
        {tr("pr.obs.cambio", {
          enlace: <Link href={`/trazas${query}`}>{t("pr.obs.abrir")}</Link>,
        })}
      </p>
    </section>
  );
}

function PasoObservado({ paso }: { paso: ObservedStep }) {
  return (
    <article className="card static">
      <div className="card-top">
        <h3>{paso.label}</h3>
        <div className="price">
          {paso.unstable ? "—" : paso.variants.length}
          <small>
            {paso.unstable
              ? t("pr.obs.no_versiones")
              : paso.concurrent
                ? t("pr.obs.llamadas")
                : paso.variants.length === 1
                  ? t("pr.obs.juego_one")
                  : t("pr.obs.juego_other")}
          </small>
        </div>
      </div>

      {paso.concurrent && <p className="disclaimer">{paso.note}</p>}
      {paso.unstable ? (
        <p className="unattributed">{paso.note}</p>
      ) : (
        <div className="ancha">
        <div className="tbl-scroll">
          <table className="tbl">
            <thead>
              <tr>
                <th>{t("pr.obs.col.instrucciones")}</th>
                <th className="r">{t("pr.col.por_ejecucion")}</th>
                <th className="r">{t("pr.col.ejecuciones")}</th>
                <th className="r hide-sm">{t("pr.obs.col.desde")}</th>
                <th className="r hide-sm">{t("pr.obs.col.hasta")}</th>
              </tr>
            </thead>
            <tbody>
              {paso.variants.map((v) => (
                <tr key={v.step_key}>
                  <td>
                    <span className="hint">{v.hint || t("pr.obs.sin_instrucciones")}</span>
                    <div className="meta pro">{v.step_key}</div>
                  </td>
                  <td className="r money">
                    {v.cost_per_execution_usd === null ? "—" : money(v.cost_per_execution_usd)}
                  </td>
                  <td className="r">{number(v.traces)}</td>
                  <td className="r hide-sm">{v.first_seen ? timestamp(v.first_seen) : "—"}</td>
                  <td className="r hide-sm">{v.last_seen ? timestamp(v.last_seen) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </div>
      )}
    </article>
  );
}

// ---------------------------------------------------------------------------------
// Crear
// ---------------------------------------------------------------------------------
