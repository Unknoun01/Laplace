"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { BackendDown, Cargando, NeedsKey, NoProject, NotYours } from "@/components/states";
import {
  deleteCustomPrice,
  deleteProject,
  getAlertSettings,
  getBudget,
  getCustomPrices,
  getInstance,
  listProjects,
  setAlertSettings,
  setBudget,
  setCustomPrice,
  testAlert,
} from "@/lib/api";
import { money } from "@/lib/format";
import { Euros, guardarTipo, leerTipo } from "@/lib/moneda";
import type { AlertSettings, Budget, CustomPrices, Instance } from "@/lib/types";
import { usePermisos } from "@/lib/permisos";
import { useApi } from "@/lib/useApi";

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

  const estado = useApi(async () => {
    const projects = await listProjects();
    if (projects.length === 0) return null;
    const project = projects.find((p) => p.id === pedido)?.id ?? projects[0].id;
    const [budget, alertas, precios, instancia] = await Promise.all([
      getBudget(project),
      getAlertSettings(project),
      getCustomPrices(),
      getInstance(),
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

function Aviso({ ok, texto }: { ok: boolean; texto: string }) {
  if (!texto) return null;
  return <p className={ok ? "vok" : "verr"}>{texto}</p>;
}

// ---------------------------------------------------------------------------------

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

const REGLAS: [string, string][] = [
  ["repeticion", "Repeticiones"],
  ["bucle", "Bucles"],
  ["modelo_caro", "Modelo caro"],
  ["contexto_fijo", "Contexto sin caché"],
];

function Alertas({ project, inicial }: { project: string; inicial: AlertSettings }) {
  const [a, setA] = useState(inicial);
  const [slack, setSlack] = useState("");
  const [webhook, setWebhook] = useState("");
  const [correo, setCorreo] = useState(inicial.email_to);
  const [umbral, setUmbral] = useState(String(inicial.min_usd));
  const [calma, setCalma] = useState(String(inicial.quiet_hours));
  const [msg, setMsg] = useState({ ok: true, texto: "" });

  async function guardar(cambios: Parameters<typeof setAlertSettings>[1], texto: string) {
    try {
      setA(await setAlertSettings(project, cambios));
      setMsg({ ok: true, texto });
      setSlack("");
      setWebhook("");
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : "no se ha podido guardar" });
    }
  }

  async function probar() {
    try {
      const r = await testAlert(project);
      setMsg(
        r.delivered
          ? { ok: true, texto: "Mensaje de prueba enviado. Míralo en el canal." }
          : { ok: false, texto: "No ha llegado por ningún canal. Revisa las direcciones." },
      );
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : "no se ha podido probar" });
    }
  }

  return (
    <section className="sec">
      <h3>Alertas</h3>
      <p className="lead">
        Un mensaje cuando un problema pasa del umbral en dinero ya gastado. Nunca se repite
        el mismo aviso dentro del periodo de calma, y lo que marques como arreglado o
        ignorado no avisa.
      </p>
      <p className={a.enabled ? "vok" : "muted"}>
        {a.muted
          ? "Silenciadas para este proyecto."
          : a.enabled
            ? "Activas."
            : "Apagadas: pon al menos un canal."}
      </p>

      <div className="canales">
        <Canal
          titulo="Slack"
          actual={a.slack}
          valor={slack}
          setValor={setSlack}
          placeholder="https://hooks.slack.com/services/…"
          guardar={() => guardar({ webhook_url: slack.trim() }, "Webhook de Slack guardado.")}
          quitar={() => guardar({ webhook_url: "" }, "Slack quitado.")}
        />
        <Canal
          titulo="Webhook (Teams, Discord, n8n…)"
          actual={a.webhook}
          valor={webhook}
          setValor={setWebhook}
          placeholder="https://…"
          guardar={() =>
            guardar({ generic_webhook_url: webhook.trim() }, "Webhook guardado.")
          }
          quitar={() => guardar({ generic_webhook_url: "" }, "Webhook quitado.")}
        />
        <div className="canal">
          <small>Correo</small>
          <div className="ab" style={{ margin: 0 }}>
            <input
              className="field grow"
              type="email"
              value={correo}
              onChange={(e) => setCorreo(e.target.value)}
              placeholder="equipo@empresa.com"
            />
            <button
              type="button"
              className="btn small"
              onClick={() =>
                guardar({ email_to: correo.trim() }, correo.trim() ? "Correo guardado." : "Correo quitado.")
              }
              disabled={!correo.trim() && !a.email_to}
            >
              {!correo.trim() && a.email_to ? "Quitar" : "Guardar"}
            </button>
          </div>
          {a.email_to && !a.email_ready && (
            <small className="verr">
              Falta el servidor de correo de la instalación: arranca Laplace con
              LAPLACE_SMTP_HOST, LAPLACE_SMTP_USER y LAPLACE_SMTP_PASSWORD.
            </small>
          )}
        </div>
      </div>

      <div className="ab">
        <label>
          <small>Umbral (dólares ya gastados)</small>
          <input
            className="field"
            inputMode="decimal"
            value={umbral}
            onChange={(e) => setUmbral(e.target.value)}
            style={{ width: 120 }}
          />
        </label>
        <label>
          <small>Calma por problema (horas)</small>
          <input
            className="field"
            inputMode="decimal"
            value={calma}
            onChange={(e) => setCalma(e.target.value)}
            style={{ width: 120 }}
          />
        </label>
        <button
          type="button"
          className="btn"
          onClick={() =>
            guardar(
              {
                threshold: Number(umbral.replace(",", ".")) || 0,
                quiet_hours: Number(calma.replace(",", ".")) || 0,
              },
              "Umbral y calma guardados.",
            )
          }
        >
          Guardar
        </button>
      </div>

      <fieldset className="reglas">
        <legend>Qué avisa</legend>
        {REGLAS.map(([clave, nombre]) => (
          <label key={clave}>
            <input
              type="checkbox"
              checked={!a.muted_kinds.includes(clave)}
              onChange={(e) =>
                guardar(
                  {
                    muted_kinds: e.target.checked
                      ? a.muted_kinds.filter((k) => k !== clave)
                      : [...a.muted_kinds, clave],
                  },
                  "Reglas guardadas.",
                )
              }
            />{" "}
            {nombre}
          </label>
        ))}
      </fieldset>

      <div className="actions" style={{ paddingTop: 14 }}>
        <button type="button" className="btn" onClick={probar} disabled={!a.enabled}>
          Enviar un mensaje de prueba
        </button>
        <button
          type="button"
          className="btn"
          onClick={() =>
            guardar({ muted: !a.muted }, a.muted ? "Alertas reactivadas." : "Alertas silenciadas.")
          }
        >
          {a.muted ? "Reactivar" : "Silenciar este proyecto"}
        </button>
      </div>
      <Aviso {...msg} />
    </section>
  );
}

function Canal(props: {
  titulo: string;
  actual: string;
  valor: string;
  setValor: (v: string) => void;
  placeholder: string;
  guardar: () => void;
  quitar: () => void;
}) {
  return (
    <div className="canal">
      <small>
        {props.titulo}
        {props.actual && <span className="puesto"> · puesto ({props.actual})</span>}
      </small>
      <div className="ab" style={{ margin: 0 }}>
        <input
          className="field grow"
          value={props.valor}
          onChange={(e) => props.setValor(e.target.value)}
          placeholder={props.actual ? "Pega otro para cambiarlo" : props.placeholder}
          aria-label={props.titulo}
        />
        <button
          type="button"
          className="btn small"
          onClick={props.guardar}
          disabled={!props.valor.trim()}
        >
          Guardar
        </button>
        {props.actual && (
          <button type="button" className="btn small" onClick={props.quitar}>
            Quitar
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------

function Tarifas({ inicial }: { inicial: CustomPrices }) {
  const [precios, setPrecios] = useState(inicial);
  const [msg, setMsg] = useState({ ok: true, texto: "" });
  const [nuevo, setNuevo] = useState({ model: "", input: "", output: "" });

  async function recargar(texto: string) {
    setPrecios(await getCustomPrices());
    setMsg({ ok: true, texto });
  }

  async function guardar(model: string, input: string, output: string) {
    try {
      const r = await setCustomPrice({
        model,
        input: Number(input.replace(",", ".")),
        output: Number(output.replace(",", ".")),
      });
      setNuevo({ model: "", input: "", output: "" });
      await recargar(
        `Tarifa de ${model} guardada. ${r.repriced_spans} llamadas ya guardadas tienen ahora su coste.`,
      );
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : "no se ha podido guardar" });
    }
  }

  async function quitar(model: string) {
    try {
      await deleteCustomPrice(model);
      await recargar(`Tarifa de ${model} quitada.`);
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : "no se ha podido quitar" });
    }
  }

  const propias = Object.entries(precios.models);
  return (
    <section className="sec">
      <h3>Tarifas propias</h3>
      <p className="lead">
        Para un modelo que no está en nuestra tabla, o uno con precio negociado. En dólares
        por millón de tokens, de la página del proveedor. Al guardarla se recalcula también
        lo que ya habías enviado. Si el modelo corre en tu máquina, no le pongas precio: no
        te cobra nadie.
      </p>
      {!precios.editable && (
        <p className="muted">
          Las tarifas valen para toda la instalación: sólo las puede cambiar una clave de
          instalación.
        </p>
      )}

      {precios.unpriced.length > 0 && (
        <p>
          Sin tarifa ahora mismo:{" "}
          {precios.unpriced.map((m) => (
            <button
              key={m}
              type="button"
              className="chip where as-btn"
              onClick={() => setNuevo({ ...nuevo, model: m })}
              disabled={!precios.editable}
            >
              {m}
            </button>
          ))}
        </p>
      )}

      {propias.length > 0 && (
        <table className="tabla-simple">
          <thead>
            <tr>
              <th>Modelo</th>
              <th>Entrada</th>
              <th>Salida</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {propias.map(([m, p]) => (
              <tr key={m}>
                <td className="num">{m}</td>
                <td className="num">{money(p.input)}</td>
                <td className="num">{money(p.output)}</td>
                <td>
                  {precios.editable && (
                    <button type="button" className="btn small" onClick={() => quitar(m)}>
                      Quitar
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {precios.editable && (
        <div className="ab">
          <label className="grow">
            <small>Modelo</small>
            <input
              className="field"
              value={nuevo.model}
              onChange={(e) => setNuevo({ ...nuevo, model: e.target.value })}
              placeholder="mi-modelo"
            />
          </label>
          <label>
            <small>Entrada ($/M)</small>
            <input
              className="field"
              inputMode="decimal"
              value={nuevo.input}
              onChange={(e) => setNuevo({ ...nuevo, input: e.target.value })}
              style={{ width: 110 }}
            />
          </label>
          <label>
            <small>Salida ($/M)</small>
            <input
              className="field"
              inputMode="decimal"
              value={nuevo.output}
              onChange={(e) => setNuevo({ ...nuevo, output: e.target.value })}
              style={{ width: 110 }}
            />
          </label>
          <button
            type="button"
            className="btn"
            disabled={!nuevo.model.trim() || !nuevo.input || !nuevo.output}
            onClick={() => guardar(nuevo.model.trim(), nuevo.input, nuevo.output)}
          >
            Guardar tarifa
          </button>
        </div>
      )}
      <Aviso {...msg} />
    </section>
  );
}

// ---------------------------------------------------------------------------------

/**
 * Tema claro u oscuro (D-125). Por defecto sigue al sistema; lo elegido aquí se guarda
 * en este navegador y lo aplica el script del layout antes del primer pintado.
 */
function Apariencia() {
  const [tema, setTema] = useState<"sistema" | "light" | "dark">("sistema");
  useEffect(() => {
    try {
      const t = window.localStorage.getItem("laplace.theme");
      if (t === "light" || t === "dark") setTema(t);
    } catch {
      /* almacenamiento bloqueado: se queda en el del sistema */
    }
  }, []);

  function elegir(valor: "sistema" | "light" | "dark") {
    setTema(valor);
    try {
      if (valor === "sistema") window.localStorage.removeItem("laplace.theme");
      else window.localStorage.setItem("laplace.theme", valor);
    } catch {
      /* dura lo que la pestaña */
    }
    if (valor === "sistema") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = valor;
  }

  return (
    <section className="sec">
      <h3>Apariencia</h3>
      <p className="lead">Se guarda en este navegador.</p>
      <div className="seg" role="group" aria-label="Tema">
        {([
          ["sistema", "Como el sistema"],
          ["light", "Claro"],
          ["dark", "Oscuro"],
        ] as const).map(([valor, nombre]) => (
          <button key={valor} type="button" aria-pressed={tema === valor} onClick={() => elegir(valor)}>
            {nombre}
          </button>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------------

function Moneda() {
  const [valor, setValor] = useState("");
  const [msg, setMsg] = useState({ ok: true, texto: "" });
  useEffect(() => {
    const t = leerTipo();
    setValor(t ? String(t).replace(".", ",") : "");
  }, []);

  return (
    <section className="sec">
      <h3>Ver también en euros</h3>
      <p className="lead">
        Los proveedores facturan en dólares y ésa sigue siendo la cifra. Si pones un tipo
        de cambio, al lado de los importes principales aparece el equivalente en euros con
        «≈». El tipo lo pones tú y se guarda sólo en este navegador: no lo descargamos de
        ninguna parte.
      </p>
      <div className="ab">
        <label>
          <small>1 $ son … €</small>
          <input
            className="field"
            inputMode="decimal"
            value={valor}
            onChange={(e) => setValor(e.target.value)}
            placeholder="0,92"
            style={{ width: 110 }}
          />
        </label>
        <button
          type="button"
          className="btn"
          onClick={() => {
            const t = Number(valor.replace(",", "."));
            guardarTipo(t > 0 ? t : null);
            setMsg({ ok: true, texto: t > 0 ? "Tipo guardado." : "Euros desactivados." })
          }}
        >
          Guardar
        </button>
        {valor && (
          <button
            type="button"
            className="btn"
            onClick={() => {
              guardarTipo(null);
              setValor("");
              setMsg({ ok: true, texto: "Euros desactivados." });
            }}
          >
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
