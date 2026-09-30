"use client";

import { inicioDeTramo, money } from "@/lib/format";
import { t } from "@/lib/textos";
import type { Grafico } from "@/lib/types";

/**
 * Dónde se va el dinero (D-152): el gasto en el tiempo con la franja evitable encima, y
 * el coste por paso con su parte evitable.
 *
 * La franja es un **reparto**, no una medida por día: las reglas miden lo evitable en
 * todo el rango, y el backend lo reparte en proporción a lo que gastó cada día el paso
 * de cada problema. Por eso el gráfico lo dice justo debajo, plegado como el resto de
 * porqués: los días suman la cifra del héroe y nada más.
 */
export function Graficos({ grafico, currency }: { grafico: Grafico; currency: string }) {
  return (
    <section className="sec graficos">
      <h2>{t("graf.titulo")}</h2>
      <GastoEnElTiempo grafico={grafico} currency={currency} />
      <CostePorPaso grafico={grafico} currency={currency} />
    </section>
  );
}

function GastoEnElTiempo({ grafico, currency }: { grafico: Grafico; currency: string }) {
  const alto = 120;
  const ancho = 720;
  // Como en el Panel: el eje empieza un tramo antes del primer gasto, y los vacíos del
  // final se quedan porque «no se ha gastado nada desde entonces» es información.
  const primero = grafico.buckets.findIndex((b) => b.cost_usd > 0);
  const tramos = grafico.buckets.slice(Math.max(primero - 1, 0));
  if (tramos.length < 2) return null;
  const tope = Math.max(...tramos.map((b) => b.cost_usd));
  const paso = ancho / tramos.length;
  const porDias = grafico.bucket_minutes >= 1440;

  return (
    <div className="chart">
      <div className="chart-head">
        <span>{porDias ? t("graf.dia") : t("graf.hora")}</span>
        <span className="legend">
          <i className="real" /> {t("graf.real")} <i className="evitable" /> {t("graf.evitable")}
        </span>
      </div>
      <svg viewBox={`0 0 ${ancho} ${alto}`} role="img" aria-label={t("graf.aria")}>
        {tramos.map((b, i) => {
          const total = tope > 0 ? (b.cost_usd / tope) * (alto - 6) : 0;
          const evitable = b.cost_usd > 0 ? (b.avoidable_usd / b.cost_usd) * total : 0;
          const x = i * paso + paso * 0.18;
          const w = paso * 0.64;
          return (
            <g key={b.start}>
              <title>
                {t("graf.punto", {
                  fecha: inicioDeTramo(b.start, grafico.bucket_minutes),
                  coste: money(b.cost_usd, currency),
                  evitable: money(b.avoidable_usd, currency),
                })}
              </title>
              <rect x={x} y={alto - total} width={w} height={total} className="real" />
              {evitable > 0 && (
                <rect x={x} y={alto - total} width={w} height={evitable} className="evitable" />
              )}
            </g>
          );
        })}
      </svg>
      <div className="chart-foot">
        <span>{inicioDeTramo(tramos[0].start, grafico.bucket_minutes)}</span>
        <span>{inicioDeTramo(tramos[tramos.length - 1].start, grafico.bucket_minutes)}</span>
      </div>
      <details className="porque pregunta">
        <summary>{t("graf.reparto")}</summary>
        <p>{t("graf.reparto.texto")}</p>
        {grafico.unattributed_usd > 0 && (
          <p>{t("graf.sin_repartir", { coste: money(grafico.unattributed_usd, currency) })}</p>
        )}
      </details>
    </div>
  );
}

function CostePorPaso({ grafico, currency }: { grafico: Grafico; currency: string }) {
  if (grafico.steps.length === 0) return null;
  const filas = [
    ...grafico.steps.map((p) => ({ key: p.key, name: p.name, coste: p.cost_usd, evitable: p.avoidable_usd })),
    ...(grafico.other_steps_usd > 0
      ? [{ key: "", name: t("graf.otros"), coste: grafico.other_steps_usd, evitable: 0 }]
      : []),
  ];
  const tope = Math.max(...filas.map((f) => f.coste));
  return (
    <div className="pasos-coste">
      <h3>{t("graf.pasos")}</h3>
      <ul>
        {filas.map((f) => (
          <li key={f.key || "otros"} className={f.key ? undefined : "otros"}>
            <span className="nombre" title={f.name}>
              {f.name}
            </span>
            <span className="barra" aria-hidden>
              <i className="real" style={{ width: `${(f.coste / tope) * 100}%` }}>
                {f.evitable > 0 && (
                  <i className="evitable" style={{ width: `${(f.evitable / f.coste) * 100}%` }} />
                )}
              </i>
            </span>
            <span className="cifra">
              {money(f.coste, currency)}
              {f.evitable > 0 && (
                <small>{t("graf.paso.evitable", { evitable: money(f.evitable, currency) })}</small>
              )}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
