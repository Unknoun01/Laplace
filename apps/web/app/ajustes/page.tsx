"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { BackendDown, Cargando, NeedsKey, NoProject, NotYours } from "@/components/states";
import {
  deleteProject,
  getAlertSettings,
  getBudget,
  getCustomPrices,
  getInstance,
  listProjects,
  setBudget,
} from "@/lib/api";
import { Euros } from "@/lib/moneda";
import type { Budget, Instance } from "@/lib/types";
import { usePermisos } from "@/lib/permisos";
import { useApi } from "@/lib/useApi";
import { Aviso } from "./aviso";
import { Tarifas } from "./tarifas";
import { Apariencia, Moneda } from "./preferencias";
import { Alertas } from "./alertas";

/**
 * Ajustes del proyecto (D-123): lo que el usuario decide y antes sólo se podía decir
 * con variables de entorno o editando ficheros del repositorio.
 *
 * Cada bloque se guarda solo y dice qué ha pasado. Nada de un «Guardar» general al
 * final que se lleva cinco cambios o ninguno.
 */
function Contenido() {
  const params = useSearchParams();
  const pedido = params.get("project") ?? "";
  const permisos = usePermisos(pedido);

  const estado = useApi(async (senal) => {
    const projects = await listProjects(senal);
    if (projects.length === 0) return null;
    const project = projects.find((p) => p.id === pedido)?.id ?? projects[0].id;
    const [budget, alertas, precios, instancia] = await Promise.all([
      getBudget(project, senal),
      getAlertSettings(project, senal),
      getCustomPrices(senal),
      getInstance(senal),
    ]);
    return { project, budget, alertas, precios, instancia };
  }, [pedido]);

  if (estado.fase === "cargando") return <Cargando />;
  if (estado.fase === "sin-backend") return <BackendDown />;
  if (estado.fase === "sin-clave") return <NeedsKey mensaje={estado.error.message} />;
  if (estado.fase === "sin-permiso") return <NotYours mensaje={estado.error.message} />;
  if (estado.fase === "error") return <BackendDown mensaje={estado.error.message} />;
  if (estado.datos === null) return <NoProject />;

  const { project, budget, alertas, precios, instancia } = estado.datos;
  return (
    <main className="reading ajustes">
      <section className="sec" style={{ paddingBottom: 0 }}>
        <h2>Ajustes de «{project}»</h2>
        <p className="lead">
          Presupuesto, avisos, tarifas y datos. Cada bloque se guarda por su cuenta.
        </p>
      </section>
      {!permisos.administrar && (
        <p className="solo-lectura">
          Presupuesto, alertas y borrar el proyecto los cambia un admin de la
          organización. Tu rol ({permisos.rol}) permite verlos.
        </p>
      )}
      {/* Deshabilitado y no escondido: saber qué avisa y a quién también le sirve a
          quien no puede cambiarlo (D-127). */}
      <fieldset className="sin-marco" disabled={!permisos.administrar}>
        <Presupuesto project={project} inicial={budget} />
        <Alertas project={project} inicial={alertas} />
      </fieldset>
      <Tarifas inicial={precios} />
      <Apariencia />
      <Moneda />
      <fieldset className="sin-marco" disabled={!permisos.administrar}>
        <Datos project={project} instancia={instancia} />
      </fieldset>
    </main>
  );
}

function Presupuesto({ project, inicial }: { project: string; inicial: Budget }) {
  const [budget, setB] = useState(inicial);
  const [valor, setValor] = useState(inicial.monthly_usd ? String(inicial.monthly_usd) : "");
  const [msg, setMsg] = useState({ ok: true, texto: "" });

  async function guardar(importe: number | null) {
    try {
      const nuevo = await setBudget(project, importe);
      setB(nuevo);
      setValor(importe ? String(importe) : "");
      setMsg({ ok: true, texto: importe ? "Presupuesto guardado." : "Presupuesto quitado." });
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : "no se ha podido guardar" });
    }
  }

  const pct = budget.ratio === null ? 0 : Math.min(budget.ratio, 1) * 100;
  return (
    <section className="sec">
      <h3>Presupuesto mensual</h3>
      <p className="lead">
        Sobre el mes natural, que es el que factura el proveedor. Avisa al 80 % y al 100 %
        por los canales de abajo, una vez cada uno por mes.
      </p>
      <p className={`budget-line ${budget.status}`}>{budget.headline}</p>
      {budget.ratio !== null && (
        <div className="sbar budget" role="img" aria-label={`${Math.round(pct)} por ciento`}>
          <i style={{ width: `${pct}%` }} />
        </div>
      )}
      <div className="ab">
        <label>
          <small>Dólares al mes</small>
          <input
            className="field"
            inputMode="decimal"
            value={valor}
            onChange={(e) => setValor(e.target.value)}
            placeholder="200"
            style={{ width: 140 }}
          />
        </label>
        <Euros usd={Number(valor.replace(",", ".")) || 0} />
        <button
          type="button"
          className="btn"
          onClick={() => guardar(Number(valor.replace(",", ".")) || null)}
          disabled={!valor}
        >
          Guardar
        </button>
        {budget.monthly_usd !== null && (
          <button type="button" className="btn" onClick={() => guardar(null)}>
            Quitar
          </button>
        )}
      </div>
      <Aviso {...msg} />
    </section>
  );
}

// ---------------------------------------------------------------------------------

function Datos({ project, instancia }: { project: string; instancia: Instance }) {
  const [confirmacion, setConfirmacion] = useState("");
  const [msg, setMsg] = useState({ ok: true, texto: "" });

  async function borrar() {
    try {
      await deleteProject(project);
      window.location.href = "/";
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : "no se ha podido borrar" });
    }
  }

  return (
    <section className="sec">
      <h3>Datos</h3>
      <p className="lead">
        {instancia.retention_days > 0
          ? `Esta instalación guarda las trazas ${instancia.retention_days} días; lo anterior se borra solo cada día.`
          : "Esta instalación guarda las trazas para siempre. Para borrar lo antiguo de forma automática, arráncala con LAPLACE_RETENTION_DAYS."}
      </p>
      <div className="peligro">
        <p>
          <strong>Borrar «{project}» entero.</strong> Trazas, anotaciones, conjuntos de
          casos, prompts, presupuesto y alertas. No se puede deshacer. Escribe el nombre del
          proyecto para confirmarlo.
        </p>
        <div className="ab" style={{ margin: 0 }}>
          <input
            className="field"
            value={confirmacion}
            onChange={(e) => setConfirmacion(e.target.value)}
            placeholder={project}
            aria-label="Nombre del proyecto para confirmar"
          />
          <button
            type="button"
            className="btn danger"
            disabled={confirmacion !== project}
            onClick={borrar}
          >
            Borrar el proyecto
          </button>
        </div>
      </div>
      <Aviso {...msg} />
    </section>
  );
}

export default function AjustesPage() {
  return (
    <Suspense fallback={<Cargando />}>
      <Contenido />
    </Suspense>
  );
}
