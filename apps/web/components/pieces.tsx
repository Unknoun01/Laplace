import Link from "next/link";
import { duration, money, tokens } from "@/lib/format";
import type { Finding, SpanType } from "@/lib/types";

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
}: {
  amount: number;
  currency: string;
  label: string;
  good?: boolean;
}) {
  return (
    <div>
      <div className={`big num${good ? " good" : ""}`}>{money(amount, currency)}</div>
      <div className="pair-lbl">{label}</div>
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
        aria-label={`${money(necessary, currency)} de coste necesario, ${money(
          avoidable,
          currency,
        )} evitable`}
      >
        <i className="keep" style={{ width: `${100 - share}%` }} />
        <i className="save" style={{ width: `${share}%` }} />
      </div>
      <div className="gaplbl">
        <span>{money(necessary, currency)} de trabajo real</span>
        <span>
          <b>{money(avoidable, currency)}</b> que estás tirando
        </span>
      </div>
    </>
  );
}

/** Tarjeta de problema: título llano, precio en ámbar, chips y línea técnica. */
export function FindingCard({ finding, href }: { finding: Finding; href: string }) {
  const flojo = !finding.costs_money;

  return (
    <Link href={href} className={`card${flojo ? " low" : ""}`}>
      <div className="card-top">
        <h3>{finding.title}</h3>
        <div className="price">
          {flojo ? duration(finding.window_waste_ms) : money(finding.monthly_saving_usd, finding.currency)}
          <small>{flojo ? "de espera evitable" : "al mes"}</small>
        </div>
      </div>
      <p>{finding.summary}</p>
      <footer>
        <span className={`chip ${finding.difficulty}`}>{finding.difficulty_label}</span>
        {finding.scope_label && <span className="chip where">{finding.scope_label}</span>}
        {flojo && <span className="chip where">No cuesta dinero, cuesta tiempo</span>}
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
