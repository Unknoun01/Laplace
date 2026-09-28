import Link from "next/link";
import { duration, money, moneyShort, tokens } from "@/lib/format";
import { Euros } from "@/lib/moneda";
import type { Finding, SpanType } from "@/lib/types";
import { tr } from "@/lib/i18n";
import { t } from "@/lib/textos";

/** Punto de color por tipo de span. El mismo código en todo el producto. */
export function KindDot({ type, failed }: { type: SpanType | string; failed?: boolean }) {
  return <i className={`kind ${failed ? "err" : type}`} aria-hidden />;
}

/** Cifra grande de dinero, con su etiqueta debajo. */
export function BigMoney({
  amount,
  currency,
  label,
  good,
  save,
}: {
  amount: number;
  currency: string;
  label: string;
  good?: boolean;
  /** Lo que se puede dejar de pagar: el mismo ámbar que su tramo de la barra. */
  save?: boolean;
}) {
  return (
    <div>
      <div className={`big num${good ? " good" : ""}${save ? " save" : ""}`}>
        {money(amount, currency)}
      </div>
      <div className="pair-lbl">
        {label} <Euros usd={amount} />
      </div>
    </div>
  );
}

/** Barra de reparto entre lo que hay que pagar y lo que sobra. */
export function GapBar({
  necessary,
  avoidable,
  currency,
}: {
  necessary: number;
  avoidable: number;
  currency: string;
}) {
  const total = necessary + avoidable;
  const share = total > 0 ? (avoidable / total) * 100 : 0;

  return (
    <>
      <div
        className="gapbar"
        role="img"
        aria-label={t("piezas.gap.aria", {
          necesario: money(necessary, currency),
          evitable: money(avoidable, currency),
        })}
      >
        <i className="keep" style={{ width: `${100 - share}%` }} />
        <i className="save" style={{ width: `${share}%` }} />
      </div>
      <div className="gaplbl">
        <span>{t("piezas.gap.real", { coste: money(necessary, currency) })}</span>
        <span>{tr("piezas.gap.tirando", { coste: <b>{money(avoidable, currency)}</b> })}</span>
      </div>
    </>
  );
}

/**
 * Tarjeta de problema: título llano, precio en ámbar, chips y línea técnica.
 *
 * El precio dice siempre **de qué periodo habla**. Cuando hay días suficientes es el
 * mes proyectado; cuando no, es el dinero ya gastado en la ventana observada, y lo
 * pone. Una cifra sin periodo se lee como el periodo que le convenga al lector.
 */
export function FindingCard({
  finding,
  href,
  rank,
  share,
}: {
  finding: Finding;
  href: string;
  /** Puesto en la lista ordenada por dinero; sin él, la tarjeta no lo enseña. */
  rank?: number;
  /** Lo que vale frente al mayor de la lista, de 0 a 1 (D-132). */
  share?: number;
}) {
  const flojo = !finding.costs_money;
  const proyecta = finding.monthly_saving_usd !== null;
  // Sin tarifa no hay dinero que enseñar, pero sí tokens: son datos medidos. Enseñar
  // «0 $» o sólo el tiempo diría que no gasta, y gasta (D-107).
  const enTokens = flojo && finding.window_waste_tokens > 0;

  return (
    <Link href={href} className={`card${flojo ? " low" : ""}${finding.state ? ` st-${finding.state}` : ""}`}>
      {rank !== undefined && (
        <span className="rank" aria-label={t("piezas.puesto", { n: rank })}>
          {rank}
        </span>
      )}
      <div className="card-top">
        <h3>{finding.title}</h3>
        <div
          className="price"
          title={finding.costs_money ? money(finding.window_waste_usd, finding.currency) : undefined}
        >
          {enTokens
            ? tokens(finding.window_waste_tokens)
            : flojo
            ? duration(finding.window_waste_ms)
            : `${finding.cost_is_floor ? "≥ " : ""}${moneyShort(
                proyecta ? finding.monthly_saving_usd! : finding.window_waste_usd,
                finding.currency,
              )}`}
          {/* Sin proyección la cifra es dinero ya gastado. La ventana concreta la dice
              una vez la cabecera de la sección: repetirla en cada tarjeta es ruido. */}
          <small>
            {enTokens
              ? t("piezas.tokens_de_mas")
              : flojo
              ? t("piezas.espera")
              : proyecta
              ? t("piezas.al_mes")
              : t("piezas.ya_gastado")}
          </small>
        </div>
      </div>
      {/* Una frase en Sencillo: el título dice qué pasa y la cifra cuánto; el párrafo
          entero repetía las dos cosas y hacía la lista de tres pantallas (D-124). */}
      <p className="simple-only">{finding.lead || finding.summary}</p>
      <p className="pro">{finding.summary}</p>
      {finding.state === "reaparecido" && finding.fix_check && (
        <p className="estado reaparecido">
          <strong>{t("piezas.reaparecido")}</strong>{" "}
          {finding.fix_check.headline}
        </p>
      )}
      {finding.state === "arreglado" && finding.fix_check && (
        <p className="estado arreglado">
          <strong>{t("piezas.marcado")}</strong> {finding.fix_check.headline}
        </p>
      )}
      {share !== undefined && (
        <div className="peso" aria-hidden>
          <i style={{ width: `${Math.round(share * 100)}%` }} />
        </div>
      )}
      <footer>
        {/* Texto con un punto de color, no una caja: con borde y fondo se leían como
            botones y no lo son. Toda la tarjeta es el enlace (D-125). */}
        <span className={`meta dificultad ${finding.difficulty}`}>{finding.difficulty_label}</span>
        {finding.scope_label && <span className="meta">{finding.scope_label}</span>}
        {/* Con tokens de más sí cuesta dinero: lo que no sabemos es cuánto. Decir «no
            cuesta dinero» ahí convertía «no lo sabemos» en «es gratis» (D-107). */}
        {enTokens && <span className="meta">{t("piezas.sin_tarifa")}</span>}
        {!flojo && finding.cost_is_floor && (
          <span className="meta">{t("piezas.suelo")}</span>
        )}
        {!flojo && finding.cost_unverified && (
          <span className="meta">{t("piezas.sin_verificar")}</span>
        )}
        <span className="meta ver" aria-hidden>
          {t("piezas.ver_arreglo")}
        </span>
      </footer>
      <div className="techline pro">
        {finding.tech.map((item) => (
          <span key={item.label}>
            {item.label} <b>{item.value}</b>
          </span>
        ))}
      </div>
    </Link>
  );
}

/** Fila de métricas técnicas del héroe. Sólo en modo avanzado. */
export function Readout({ items }: { items: [string, string][] }) {
  return (
    <div className="readout pro">
      {items.map(([value, label]) => (
        <div key={label}>
          <b className="num">{value}</b>
          <small>{label}</small>
        </div>
      ))}
    </div>
  );
}

export function Tokens({ value }: { value: number }) {
  return <>{tokens(value)}</>;
}
