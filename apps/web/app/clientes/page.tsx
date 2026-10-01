"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { BackendDown, Cargando, NeedsKey, NoProject, NotYours } from "@/components/states";
import {
  getCustomers,
  getExchangeRates,
  getStripe,
  listProjects,
  parseDays,
  setCustomerRevenue,
  setExchangeRates,
  setStripe,
  syncStripe,
} from "@/lib/api";
import { money, number, porcentaje, timestamp, windowLabel } from "@/lib/format";
import { usePermisos } from "@/lib/permisos";
import type { CustomerMargin, MarginView, StripeStatus } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { t, tn } from "@/lib/textos";

/**
 * Margen por cliente (Fase 6, D-161): lo que te paga cada cliente frente a lo que te
 * cuesta su trabajo, al mes.
 *
 * Lo primero es el aviso —quién te hace perder dinero—, porque es la pregunta que
 * ninguna otra herramienta contesta y la que se viene a hacer. Debajo, la tabla con lo
 * que se puede escribir (lo que paga cada uno) al lado de lo que se mide.
 */
function Contenido() {
  const params = useSearchParams();
  const pedido = params.get("project") ?? "";
  // El mismo rango que el resto: el margen siempre es al mes, proyectado desde él.
  const days = parseDays(params.get("days") ?? undefined);
  const [version, setVersion] = useState(0);

  const estado = useApi(async (senal) => {
    const proyectos = await listProjects(senal);
    if (proyectos.length === 0) return null;
    const id = proyectos.find((p) => p.id === pedido)?.id ?? proyectos[0].id;
    return getCustomers(id, days, senal);
  }, [pedido, days, version]);
  const permisos = usePermisos(estado.fase === "listo" ? estado.datos?.project_id ?? "" : "");

  if (estado.fase === "cargando") return <Cargando />;
  if (estado.fase === "sin-backend") return <BackendDown />;
  if (estado.fase === "sin-clave") return <NeedsKey mensaje={estado.error.message} codigo={estado.error.code} />;
  if (estado.fase === "sin-permiso") return <NotYours mensaje={estado.error.message} />;
  if (estado.fase === "error") return <BackendDown mensaje={estado.error.message} />;
  const vista = estado.datos;
  if (!vista) return <NoProject />;

  const recargar = () => setVersion((v) => v + 1);
  const query = `?project=${encodeURIComponent(vista.project_id)}&days=${days}`;
  const pierden = vista.customers.filter((c) => c.status === "pierde");

  return (
    <main className="reading clientes">
      <section className="hero">
        <h1>{t("cl.titulo", { proyecto: vista.project_id })}</h1>
        <p className="lead">{t("cl.lead")}</p>
        <p className="cl-titular">{vista.headline}</p>
      </section>

      {pierden.length > 0 && (
        <section className="cl-aviso" role="alert">
          <h2>{tn("cl.aviso", pierden.length)}</h2>
          <ul>
            {pierden.map((c) => (
              <li key={c.customer_id}>
                <strong>{c.customer_id}</strong> — {c.headline}
                <Problemas cliente={c} query={query} />
              </li>
            ))}
          </ul>
          <p>{t("cl.aviso.texto")}</p>
        </section>
      )}

      {vista.customers.length > 0 && (
        <section className="sec">
          <div className="tbl-scroll">
            <table className="tbl cl-tabla">
              <thead>
                <tr>
                  <th>{t("cl.h.cliente")}</th>
                  <th className="num">{t("cl.h.ejecuciones")}</th>
                  <th className="num">{t("cl.h.coste")}</th>
                  <th className="num">{t("cl.h.ingresos")}</th>
                  <th className="num">{t("cl.h.margen")}</th>
                  <th className="num">{t("cl.h.evitable")}</th>
                </tr>
              </thead>
              <tbody>
                {vista.customers.map((c) => (
                  <Fila
                    key={c.customer_id}
                    cliente={c}
                    vista={vista}
                    escribir={permisos.escribir}
                    query={query}
                    onChange={recargar}
                  />
                ))}
              </tbody>
            </table>
          </div>
          {vista.projected && (
            <p className="disclaimer">
              {t("diag.al_ritmo", { ventana: windowLabel(vista.observed_days) })}
            </p>
          )}
        </section>
      )}

      {vista.unassigned_traces > 0 && (
        <p className="muted">
          {t("cl.sin_cliente", {
            coste: money(vista.unassigned_cost_usd, vista.currency),
            n: number(vista.unassigned_traces),
          })}
        </p>
      )}

      {permisos.escribir && <Stripe project={vista.project_id} onChange={recargar} />}

      {permisos.escribir && <TiposDeCambio project={vista.project_id} onChange={recargar} />}

      {permisos.escribir && <Anadir project={vista.project_id} onChange={recargar} />}

      <section className="sec">
        <h2>{t("cl.como.titulo")}</h2>
        <p className="lead">{t("cl.como.texto")}</p>
        <pre>{`import laplace
laplace.set_context(customer_id="acme")`}</pre>
      </section>
    </main>
  );
}

const CHIP: Record<CustomerMargin["status"], string> = {
  pierde: "hard",
  ajustado: "mid",
  gana: "easy",
  "sin-ingresos": "where",
  "sin-proyeccion": "where",
  "sin-trafico": "where",
  "sin-cambio": "where",
};

/** Las monedas que se ofrecen al poner lo que paga un cliente. Otra cualquiera de tres
 * letras se puede poner desde la API; aquí van las habituales (D-179). */
const MONEDAS = ["USD", "EUR", "GBP", "MXN", "BRL", "ARS", "COP", "CLP", "CAD", "AUD", "CHF", "JPY"];

function SelectorMoneda({
  valor,
  onChange,
  etiqueta,
}: {
  valor: string;
  onChange: (moneda: string) => void;
  etiqueta: string;
}) {
  const opciones = MONEDAS.includes(valor) ? MONEDAS : [valor, ...MONEDAS];
  return (
    <select
      className="field cl-moneda"
      value={valor}
      aria-label={etiqueta}
      onChange={(e) => onChange(e.target.value)}
    >
      {opciones.map((m) => (
        <option key={m} value={m}>
          {m}
        </option>
      ))}
    </select>
  );
}

function Fila({
  cliente,
  vista,
  escribir,
  query,
  onChange,
}: {
  cliente: CustomerMargin;
  vista: MarginView;
  escribir: boolean;
  query: string;
  onChange: () => void;
}) {
  const suelo = cliente.cost_is_floor ? "≥ " : "";
  const techo = cliente.cost_is_floor ? "≤ " : "";
  return (
    <>
      <tr className={`cl-fila ${cliente.status}`}>
        <td>
          <strong>{cliente.customer_id}</strong>{" "}
          <span className={`chip ${CHIP[cliente.status]}`}>{t(`cl.estado.${cliente.status}`)}</span>
        </td>
        <td className="num">{number(cliente.traces)}</td>
        <td className="num">
          {cliente.monthly_cost_usd !== null
            ? suelo + money(cliente.monthly_cost_usd, vista.currency)
            : "—"}
        </td>
        <td className="num">
          {cliente.revenue_source === "stripe" && (
            <span className="chip where cl-fuente">{t("cl.stripe.fuente")}</span>
          )}
          {escribir ? (
            <Ingreso cliente={cliente} project={vista.project_id} onChange={onChange} />
          ) : cliente.revenue_amount !== null ? (
            money(cliente.revenue_amount, cliente.revenue_currency)
          ) : (
            "—"
          )}
          {cliente.revenue_currency !== vista.currency && cliente.monthly_revenue !== null && (
            <small>≈ {money(cliente.monthly_revenue, vista.currency)}</small>
          )}
        </td>
        <td className={`num margen ${cliente.status}`}>
          {cliente.margin_usd !== null ? (
            <>
              {(cliente.margin_usd < 0 ? "−" : techo) +
                money(Math.abs(cliente.margin_usd), vista.currency)}
              {cliente.margin_ratio !== null && (
                <small>{porcentaje(cliente.margin_ratio)}</small>
              )}
            </>
          ) : (
            "—"
          )}
        </td>
        {/* Lo evitable suyo: el dinero de sus problemas repartido por lo que gastó en
            cada paso (D-179). Es un reparto, y la frase de abajo lo dice. */}
        <td className="num">
          {cliente.avoidable_monthly_usd !== null
            ? money(cliente.avoidable_monthly_usd, vista.currency)
            : cliente.avoidable_usd > 0
              ? money(cliente.avoidable_usd, vista.currency)
              : "—"}
        </td>
      </tr>
      <tr className="cl-frase">
        <td colSpan={6}>
          {cliente.headline}
          <Problemas cliente={cliente} query={query} />
        </td>
      </tr>
    </>
  );
}

function Ingreso({
  cliente,
  project,
  onChange,
}: {
  cliente: CustomerMargin;
  project: string;
  onChange: () => void;
}) {
  // Lo que paga tal como se puso, en su moneda: no la conversión a dólares.
  const inicial = cliente.revenue_amount !== null ? String(cliente.revenue_amount) : "";
  const [valor, setValor] = useState(inicial.replace(".", ","));
  const [moneda, setMoneda] = useState(cliente.revenue_currency || "USD");
  const [error, setError] = useState("");
  const cambiado =
    valor.replace(",", ".") !== inicial || moneda !== (cliente.revenue_currency || "USD");

  async function guardar() {
    const numero = valor.trim() === "" ? null : Number(valor.replace(",", "."));
    if (numero !== null && (!Number.isFinite(numero) || numero < 0)) return;
    try {
      await setCustomerRevenue(project, cliente.customer_id, numero || null, moneda);
      setError("");
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("cl.error.guardar"));
    }
  }

  return (
    <span className="cl-ingreso">
      <input
        className="field"
        inputMode="decimal"
        value={valor}
        placeholder="—"
        aria-label={t("cl.ingresos.aria", { cliente: cliente.customer_id })}
        onChange={(e) => setValor(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && guardar()}
      />
      <SelectorMoneda
        valor={moneda}
        onChange={setMoneda}
        etiqueta={t("cl.moneda.aria", { cliente: cliente.customer_id })}
      />
      {cambiado && (
        <button type="button" className="btn small" onClick={guardar}>
          {t("cl.guardar")}
        </button>
      )}
      {error && <small className="verr">{error}</small>}
    </span>
  );
}

/**
 * Lo que paga cada cliente, traído de las facturas pagadas de Stripe (D-162). La clave
 * se escribe una vez y no vuelve: la API sólo devuelve sus cuatro últimos caracteres.
 */
function Stripe({ project, onChange }: { project: string; onChange: () => void }) {
  const [estado, setEstado] = useState<StripeStatus | null>(null);
  const [clave, setClave] = useState("");
  const [mensaje, setMensaje] = useState({ ok: true, texto: "" });
  const [trayendo, setTrayendo] = useState(false);

  useEffect(() => {
    getStripe(project)
      .then(setEstado)
      .catch(() => setEstado(null));
  }, [project]);

  async function guardar(valor: string | null) {
    try {
      setEstado(await setStripe(project, valor));
      setClave("");
      setMensaje({ ok: true, texto: "" });
    } catch (e) {
      setMensaje({ ok: false, texto: e instanceof Error ? e.message : t("cl.error.guardar") });
    }
  }

  async function traer() {
    setTrayendo(true);
    try {
      const r = await syncStripe(project);
      setMensaje({ ok: true, texto: r.detail });
      setEstado(await getStripe(project));
      onChange();
    } catch (e) {
      setMensaje({ ok: false, texto: e instanceof Error ? e.message : t("cl.error.guardar") });
    } finally {
      setTrayendo(false);
    }
  }

  if (!estado) return null;
  return (
    <section className="sec cl-stripe">
      <h2>{t("cl.stripe.titulo")}</h2>
      <p className="lead">{t("cl.stripe.lead")}</p>
      {estado.configured ? (
        <>
          <p className="muted">
            {t("cl.stripe.puesta", { pista: estado.key_hint })}{" "}
            {estado.last_sync
              ? t("cl.stripe.ultima", { fecha: timestamp(estado.last_sync), n: estado.customers })
              : t("cl.stripe.nunca")}
          </p>
          <div className="actions" style={{ paddingTop: 0 }}>
            <button type="button" className="btn primary" onClick={traer} disabled={trayendo}>
              {t("cl.stripe.traer")}
            </button>
            <button type="button" className="btn" onClick={() => guardar(null)}>
              {t("cl.stripe.quitar")}
            </button>
          </div>
        </>
      ) : (
        <div className="ab">
          <label>
            <small>{t("cl.stripe.clave")}</small>
            <input
              className="field"
              type="password"
              autoComplete="off"
              placeholder="rk_live_…"
              value={clave}
              onChange={(e) => setClave(e.target.value)}
            />
          </label>
          <button type="button" className="btn" onClick={() => guardar(clave.trim())}>
            {t("cl.guardar")}
          </button>
        </div>
      )}
      {mensaje.texto && <p className={mensaje.ok ? "okline" : "verr"}>{mensaje.texto}</p>}
    </section>
  );
}

/**
 * Por dónde empezar con un cliente: los problemas del Diagnóstico que pasan en sus
 * ejecuciones (los que más devuelven) y sus ejecuciones en el explorador (D-161).
 */
function Problemas({ cliente, query }: { cliente: CustomerMargin; query: string }) {
  if (cliente.traces === 0) return null;
  return (
    <span className="cl-problemas">
      {cliente.findings.length > 0 && (
        <>
          {t("cl.problemas")}{" "}
          {cliente.findings.map((f, i) => (
            <span key={f.id}>
              {i > 0 && " · "}
              <Link href={`/problema${query}&id=${encodeURIComponent(f.id)}`}>{f.title}</Link>
              {f.avoidable_usd > 0 && (
                <span className="muted"> ({t("cl.suyo", { dinero: money(f.avoidable_usd) })})</span>
              )}
            </span>
          ))}
          {" · "}
        </>
      )}
      <Link href={`/trazas${query}&customer=${encodeURIComponent(cliente.customer_id)}`}>
        {t("cl.ver_ejecuciones")}
      </Link>
    </span>
  );
}

/** Un cliente con contrato y sin ejecuciones todavía: su ingreso se puede poner ya. */
function Anadir({ project, onChange }: { project: string; onChange: () => void }) {
  const [cliente, setCliente] = useState("");
  const [importe, setImporte] = useState("");
  const [moneda, setMoneda] = useState("USD");
  const [error, setError] = useState("");

  async function guardar() {
    const numero = Number(importe.replace(",", "."));
    if (!cliente.trim() || !Number.isFinite(numero) || numero <= 0) return;
    try {
      await setCustomerRevenue(project, cliente.trim(), numero, moneda);
      setCliente("");
      setImporte("");
      setError("");
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("cl.error.guardar"));
    }
  }

  return (
    <details className="sec cl-anadir">
      <summary>{t("cl.anadir")}</summary>
      <div className="ab">
        <label>
          <small>{t("cl.anadir.id")}</small>
          <input className="field" value={cliente} onChange={(e) => setCliente(e.target.value)} />
        </label>
        <label>
          <small>{t("cl.anadir.importe")}</small>
          <input
            className="field"
            inputMode="decimal"
            value={importe}
            onChange={(e) => setImporte(e.target.value)}
          />
        </label>
        <label>
          <small>{t("cl.moneda")}</small>
          <SelectorMoneda valor={moneda} onChange={setMoneda} etiqueta={t("cl.moneda")} />
        </label>
        <button type="button" className="btn" onClick={guardar}>
          {t("cl.guardar")}
        </button>
      </div>
      {error && <p className="verr">{error}</p>}
    </details>
  );
}

/**
 * Los tipos de cambio del proyecto (D-179): cuántos dólares vale una unidad de cada
 * moneda en la que pagan tus clientes. Los pone el usuario; el producto no tiene fuente
 * de tipos y no se inventa uno.
 */
function TiposDeCambio({ project, onChange }: { project: string; onChange: () => void }) {
  const [filas, setFilas] = useState<{ moneda: string; tipo: string }[] | null>(null);
  const [mensaje, setMensaje] = useState({ ok: true, texto: "" });

  useEffect(() => {
    getExchangeRates(project)
      .then((r) =>
        setFilas(
          Object.entries(r.rates).map(([moneda, tipo]) => ({
            moneda,
            tipo: String(tipo).replace(".", ","),
          })),
        ),
      )
      .catch(() => setFilas([]));
  }, [project]);

  async function guardar() {
    if (!filas) return;
    const rates: Record<string, number> = {};
    for (const f of filas) {
      if (!f.moneda.trim() || !f.tipo.trim()) continue;
      rates[f.moneda.trim().toUpperCase()] = Number(f.tipo.replace(",", "."));
    }
    try {
      await setExchangeRates(project, rates);
      setMensaje({ ok: true, texto: t("cl.cambio.guardado") });
      onChange();
    } catch (e) {
      setMensaje({ ok: false, texto: e instanceof Error ? e.message : t("cl.error.guardar") });
    }
  }

  if (!filas) return null;
  const cambiar = (i: number, campo: "moneda" | "tipo", valor: string) =>
    setFilas(filas.map((f, j) => (j === i ? { ...f, [campo]: valor } : f)));
  return (
    <details className="sec cl-cambio" open={filas.length > 0}>
      <summary>{t("cl.cambio.titulo")}</summary>
      <p className="lead">{t("cl.cambio.lead")}</p>
      {filas.map((f, i) => (
        <div className="ab" key={i}>
          <label>
            <small>{t("cl.moneda")}</small>
            <input
              className="field"
              maxLength={3}
              value={f.moneda}
              placeholder="EUR"
              onChange={(e) => cambiar(i, "moneda", e.target.value)}
            />
          </label>
          <label>
            <small>{t("cl.cambio.tipo", { moneda: f.moneda.toUpperCase() || "…" })}</small>
            <input
              className="field"
              inputMode="decimal"
              value={f.tipo}
              placeholder="1,08"
              onChange={(e) => cambiar(i, "tipo", e.target.value)}
            />
          </label>
          <button
            type="button"
            className="btn small"
            onClick={() => setFilas(filas.filter((_, j) => j !== i))}
          >
            {t("comun.quitar")}
          </button>
        </div>
      ))}
      <div className="actions" style={{ paddingTop: 0 }}>
        <button
          type="button"
          className="btn"
          onClick={() => setFilas([...filas, { moneda: "", tipo: "" }])}
        >
          {t("cl.cambio.anadir")}
        </button>
        <button type="button" className="btn primary" onClick={guardar}>
          {t("cl.guardar")}
        </button>
      </div>
      {mensaje.texto && <p className={mensaje.ok ? "okline" : "verr"}>{mensaje.texto}</p>}
    </details>
  );
}

export default function ClientesPage() {
  return (
    <Suspense fallback={<Cargando />}>
      <Contenido />
    </Suspense>
  );
}
