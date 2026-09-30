"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { BackendDown, Cargando, NeedsKey, NoProject, NotYours } from "@/components/states";
import {
  type Retencion,
  deleteProject,
  deleteSubject,
  getAlertSettings,
  getBudget,
  getCustomPrices,
  getInstance,
  getRetention,
  listProjects,
  setBudget,
  setRetention,
} from "@/lib/api";
import { Euros } from "@/lib/moneda";
import type { Budget, Instance } from "@/lib/types";
import { usePermisos } from "@/lib/permisos";
import { useApi } from "@/lib/useApi";
import { Aviso } from "./aviso";
import { Tarifas } from "./tarifas";
import { Apariencia, Moneda } from "./preferencias";
import { Alertas } from "./alertas";
import { t } from "@/lib/textos";

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
    const [budget, alertas, precios, instancia, retencion] = await Promise.all([
      getBudget(project, senal),
      getAlertSettings(project, senal),
      getCustomPrices(senal),
      getInstance(senal),
      getRetention(project, senal),
    ]);
    return { project, budget, alertas, precios, instancia, retencion };
  }, [pedido]);

  if (estado.fase === "cargando") return <Cargando />;
  if (estado.fase === "sin-backend") return <BackendDown />;
  if (estado.fase === "sin-clave") return <NeedsKey mensaje={estado.error.message} codigo={estado.error.code} />;
  if (estado.fase === "sin-permiso") return <NotYours mensaje={estado.error.message} />;
  if (estado.fase === "error") return <BackendDown mensaje={estado.error.message} />;
  if (estado.datos === null) return <NoProject />;

  const { project, budget, alertas, precios, instancia, retencion } = estado.datos;
  return (
    <main className="reading ajustes">
      <section className="sec" style={{ paddingBottom: 0 }}>
        <h2>{t("aj.titulo", { proyecto: project })}</h2>
        <p className="lead">{t("aj.lead")}</p>
      </section>
      {!permisos.administrar && (
        <p className="solo-lectura">
          {t("aj.solo_lectura", { rol: permisos.rol ?? "" })}
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
        <Datos project={project} instancia={instancia} retencion={retencion} />
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
      setMsg({ ok: true, texto: importe ? t("aj.pres.guardado") : t("aj.pres.quitado") });
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : t("seg.error.guardar") });
    }
  }

  const pct = budget.ratio === null ? 0 : Math.min(budget.ratio, 1) * 100;
  return (
    <section className="sec">
      <h3>{t("aj.pres.titulo")}</h3>
      <p className="lead">{t("aj.pres.lead")}</p>
      <p className={`budget-line ${budget.status}`}>{budget.headline}</p>
      {budget.ratio !== null && (
        <div className="sbar budget" role="img" aria-label={t("avisos.pct_aria", { n: Math.round(pct) })}>
          <i style={{ width: `${pct}%` }} />
        </div>
      )}
      <div className="ab">
        <label>
          <small>{t("aj.pres.dolares")}</small>
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
          {t("comun.guardar")}
        </button>
        {budget.monthly_usd !== null && (
          <button type="button" className="btn" onClick={() => guardar(null)}>
            {t("comun.quitar")}
          </button>
        )}
      </div>
      <Aviso {...msg} />
    </section>
  );
}

// ---------------------------------------------------------------------------------

function Datos({
  project,
  instancia,
  retencion: inicial,
}: {
  project: string;
  instancia: Instance;
  retencion: Retencion;
}) {
  const [confirmacion, setConfirmacion] = useState("");
  const [msg, setMsg] = useState({ ok: true, texto: "" });
  const [retencion, setR] = useState(inicial);
  const [dias, setDias] = useState(inicial.days ? String(inicial.days) : "");
  const [quien, setQuien] = useState<"user_id" | "customer_id">("user_id");
  const [sujeto, setSujeto] = useState("");
  const [confirmaSujeto, setConfirmaSujeto] = useState("");

  async function guardarDias(n: number) {
    try {
      const nueva = await setRetention(project, n);
      setR(nueva);
      setDias(n ? String(n) : "");
      setMsg({
        ok: true,
        texto: nueva.effective_days
          ? t("aj.ret.guardado", { n: nueva.effective_days })
          : t("aj.ret.quitado"),
      });
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : t("seg.error.guardar") });
    }
  }

  async function borrarSujeto() {
    try {
      const r = await deleteSubject(project, quien, sujeto);
      setMsg({ ok: true, texto: t("aj.sujeto.borrado", { n: r.deleted_traces, id: sujeto }) });
      setSujeto("");
      setConfirmaSujeto("");
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : t("aj.error.borrar") });
    }
  }

  async function borrar() {
    try {
      await deleteProject(project);
      window.location.href = "/";
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : t("aj.error.borrar") });
    }
  }

  return (
    <section className="sec">
      <h3>{t("aj.datos")}</h3>
      <p className="lead">
        {instancia.retention_days > 0
          ? t("aj.datos.retencion", { n: instancia.retention_days })
          : t("aj.datos.siempre")}
      </p>
      <div className="ab">
        <label>
          <small>{t("aj.ret.dias")}</small>
          <input
            className="field"
            inputMode="numeric"
            value={dias}
            onChange={(e) => setDias(e.target.value.replace(/\D/g, ""))}
            placeholder={instancia.retention_days ? String(instancia.retention_days) : "90"}
            style={{ width: 100 }}
          />
        </label>
        <button type="button" className="btn" disabled={!dias} onClick={() => guardarDias(Number(dias))}>
          {t("comun.guardar")}
        </button>
        {retencion.days > 0 && (
          <button type="button" className="btn" onClick={() => guardarDias(0)}>
            {t("comun.quitar")}
          </button>
        )}
      </div>
      <p className="muted">
        {retencion.days > 0 && retencion.installation_days > 0 && retencion.days > retencion.installation_days
          ? t("aj.ret.manda_instalacion", { n: retencion.installation_days })
          : t("aj.ret.explica")}
      </p>
      <div className="peligro">
        <p>
          <strong>{t("aj.sujeto.titulo")}</strong> {t("aj.sujeto.texto")}
        </p>
        <div className="ab" style={{ margin: 0 }}>
          <select
            className="field"
            value={quien}
            onChange={(e) => setQuien(e.target.value as "user_id" | "customer_id")}
            aria-label={t("aj.sujeto.tipo")}
          >
            <option value="user_id">{t("aj.sujeto.persona")}</option>
            <option value="customer_id">{t("aj.sujeto.cliente")}</option>
          </select>
          <input
            className="field"
            value={sujeto}
            onChange={(e) => setSujeto(e.target.value)}
            placeholder={quien === "user_id" ? "u-07" : "acme"}
            aria-label={t("aj.sujeto.id")}
          />
          <input
            className="field"
            value={confirmaSujeto}
            onChange={(e) => setConfirmaSujeto(e.target.value)}
            placeholder={t("aj.sujeto.repite")}
            aria-label={t("aj.sujeto.repite")}
          />
          <button
            type="button"
            className="btn danger"
            disabled={!sujeto || confirmaSujeto !== sujeto}
            onClick={borrarSujeto}
          >
            {t("aj.sujeto.boton")}
          </button>
        </div>
      </div>
      <div className="peligro">
        <p>
          <strong>{t("aj.borrar.titulo", { proyecto: project })}</strong>{" "}
          {t("aj.borrar.texto")}
        </p>
        <div className="ab" style={{ margin: 0 }}>
          <input
            className="field"
            value={confirmacion}
            onChange={(e) => setConfirmacion(e.target.value)}
            placeholder={project}
            aria-label={t("aj.borrar.aria")}
          />
          <button
            type="button"
            className="btn danger"
            disabled={confirmacion !== project}
            onClick={borrar}
          >
            {t("aj.borrar.boton")}
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
