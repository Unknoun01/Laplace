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

/**
 * Tarjeta de problema: título llano, precio en ámbar, chips y línea técnica.
 *
 * El precio dice siempre **de qué periodo habla**. Cuando hay días suficientes es el
 * mes proyectado; cuando no, es el dinero ya gastado en la ventana observada, y lo
 * pone. Una cifra sin periodo se lee como el periodo que le convenga al lector.
 */
export function FindingCard({ finding, href }: { finding: Finding; href: string }) {
  const flojo = !finding.costs_money;
  const proyecta = finding.monthly_saving_usd !== null;
  // Sin tarifa no hay dinero que enseñar, pero sí tokens: son datos medidos. Enseñar
  // «0 $» o sólo el tiempo diría que no gasta, y gasta (D-107).
  const enTokens = flojo && finding.window_waste_tokens > 0;

  return (
    <Link href={href} className={`card${flojo ? " low" : ""}`}>
      <div className="card-top">
        <h3>{finding.title}</h3>
        <div className="price">
          {enTokens
            ? tokens(finding.window_waste_tokens)
            : flojo
            ? duration(finding.window_waste_ms)
            : `${finding.cost_is_floor ? "≥ " : ""}${money(
                proyecta ? finding.monthly_saving_usd! : finding.window_waste_usd,
                finding.currency,
              )}`}
          {/* Sin proyección la cifra es dinero ya gastado. La ventana concreta la dice
              una vez la cabecera de la sección: repetirla en cada tarjeta es ruido. */}
          <small>
            {enTokens
              ? "tokens de más"
              : flojo
              ? "de espera evitable"
              : proyecta
              ? "al mes"
              : "ya gastado"}
          </small>
        </div>
      </div>
      <p>{finding.summary}</p>
      <footer>
        <span className={`chip ${finding.difficulty}`}>{finding.difficulty_label}</span>
        {finding.scope_label && <span className="chip where">{finding.scope_label}</span>}
        {flojo && <span className="chip where">No cuesta dinero, cuesta tiempo</span>}
        {!flojo && finding.cost_is_floor && (
          <span className="chip where">Es un suelo: el coste real puede ser mayor</span>
        )}
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
