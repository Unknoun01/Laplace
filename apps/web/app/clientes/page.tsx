"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { BackendDown, Cargando, NeedsKey, NoProject, NotYours } from "@/components/states";
import { getCustomers, listProjects, parseDays, setCustomerRevenue } from "@/lib/api";
import { money, number, porcentaje, windowLabel } from "@/lib/format";
import { usePermisos } from "@/lib/permisos";
import type { CustomerMargin, MarginView } from "@/lib/types";
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
                </tr>
              </thead>
              <tbody>
                {vista.customers.map((c) => (
                  <Fila
                    key={c.customer_id}
                    cliente={c}
                    vista={vista}
                    escribir={permisos.escribir}
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
};

function Fila({
  cliente,
  vista,
  escribir,
  onChange,
}: {
  cliente: CustomerMargin;
  vista: MarginView;
  escribir: boolean;
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
          {escribir ? (
            <Ingreso cliente={cliente} project={vista.project_id} onChange={onChange} />
          ) : cliente.monthly_revenue !== null ? (
            money(cliente.monthly_revenue, vista.currency)
          ) : (
            "—"
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
      </tr>
      <tr className="cl-frase">
        <td colSpan={5}>{cliente.headline}</td>
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
  const inicial = cliente.monthly_revenue !== null ? String(cliente.monthly_revenue) : "";
  const [valor, setValor] = useState(inicial.replace(".", ","));
  const [error, setError] = useState("");
  const cambiado = valor.replace(",", ".") !== inicial;

  async function guardar() {
    const numero = valor.trim() === "" ? null : Number(valor.replace(",", "."));
    if (numero !== null && (!Number.isFinite(numero) || numero < 0)) return;
    try {
      await setCustomerRevenue(project, cliente.customer_id, numero || null);
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
      {cambiado && (
        <button type="button" className="btn small" onClick={guardar}>
          {t("cl.guardar")}
        </button>
      )}
      {error && <small className="verr">{error}</small>}
    </span>
  );
}

/** Un cliente con contrato y sin ejecuciones todavía: su ingreso se puede poner ya. */
function Anadir({ project, onChange }: { project: string; onChange: () => void }) {
  const [cliente, setCliente] = useState("");
  const [importe, setImporte] = useState("");
  const [error, setError] = useState("");

  async function guardar() {
    const numero = Number(importe.replace(",", "."));
    if (!cliente.trim() || !Number.isFinite(numero) || numero <= 0) return;
    try {
      await setCustomerRevenue(project, cliente.trim(), numero);
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
        <button type="button" className="btn" onClick={guardar}>
          {t("cl.guardar")}
        </button>
      </div>
      {error && <p className="verr">{error}</p>}
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
