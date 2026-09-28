"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { BackendDown, Cargando, NeedsKey, NoProject, NotYours } from "@/components/states";
import {
  ApiError,
  createPrompt,
  getOverview,
  getPrompt,
  getPrompts,
  listProjects,
  parseDays,
  setProduction,
} from "@/lib/api";
import { money, number, timestamp, tokens } from "@/lib/format";
import { usePermisos } from "@/lib/permisos";
import type { Finding, PromptCard, PromptsView, Rate, VersionMetrics } from "@/lib/types";
import { Detalle } from "./detalle";
import { SinAdoptar, Observados } from "./observados";
import { tr } from "@/lib/i18n";
import { porcentaje } from "@/lib/format";
import { t, tn } from "@/lib/textos";

/**
 * Prompts: qué versión está en producción, qué cambió entre una y otra, y —lo único
 * que no tiene ningún otro gestor de prompts— **qué costó y qué acertó cada versión
 * sobre el tráfico real que la usó**.
 *
 * Sin esa segunda mitad, esta pantalla sería un repositorio de texto con diff, que ya
 * tiene cualquiera y no hace falta que sea de Laplace. Con ella, es la única pantalla
 * del producto donde lo que un prompt cuesta y lo que acierta se ven juntos.
 *
 * Y si el usuario no ha adoptado la gestión de prompts, **la pestaña no se queda en
 * blanco**: enseña los juegos de instrucciones que se ven en sus trazas, cuándo
 * cambiaron y qué costaron. Es menos, y se dice que es menos, pero es suyo y es real.
 */
function Contenido() {
  const params = useSearchParams();
  const pedido = params.get("project") ?? "";
  const days = parseDays(params.get("days") ?? undefined);

  const [project, setProject] = useState("");
  const [fase, setFase] = useState<
    "cargando" | "listo" | "sin-proyecto" | "caido" | "sin-clave" | "sin-permiso"
  >("cargando");
  const [error, setError] = useState("");
  const [codigo, setCodigo] = useState("");
  const [vista, setVista] = useState<PromptsView | null>(null);

  const [hallazgos, setHallazgos] = useState<Finding[]>([]);

  const recargar = useCallback(
    async (id: string) => {
      // Prompts es una fuente más de hallazgos (D-157): si el Diagnóstico tiene uno
      // abierto de un prompt, su ficha lo dice. Se pide aparte y no se espera: con
      // volumen tarda segundos, y si falla la pestaña sigue igual.
      getOverview(id, days)
        .then((vista) => setHallazgos(vista.findings.filter((f) => f.kind === "prompt_caro")))
        .catch(() => setHallazgos([]));
      setVista(await getPrompts(id, days));
    },
    [days],
  );

  useEffect(() => {
    let vigente = true;
    (async () => {
      try {
        const proyectos = await listProjects();
        if (!vigente) return;
        if (proyectos.length === 0) return setFase("sin-proyecto");
        const id = proyectos.find((p) => p.id === pedido)?.id ?? proyectos[0].id;
        setProject(id);
        await recargar(id);
        if (vigente) setFase("listo");
      } catch (e) {
        if (!vigente) return;
        setError(e instanceof Error ? e.message : "");
        setCodigo(e instanceof ApiError ? e.code : "");
        // Igual que en el resto de pantallas: «te falta la clave» y «esa clave no es de
        // este proyecto» no son fallos del backend y tienen salida propia.
        const estado = e instanceof ApiError ? e.status : 0;
        setFase(estado === 401 ? "sin-clave" : estado === 403 ? "sin-permiso" : "caido");
      }
    })();
    return () => {
      vigente = false;
    };
  }, [pedido, recargar]);

  if (fase === "cargando") return <Cargando />;
  if (fase === "caido") return <BackendDown mensaje={error} />;
  if (fase === "sin-clave") return <NeedsKey mensaje={error} codigo={codigo} />;
  if (fase === "sin-permiso") return <NotYours mensaje={error} />;
  if (fase === "sin-proyecto") return <NoProject />;
  if (!vista) return <Cargando />;

  const query = `?project=${encodeURIComponent(project)}&days=${days}`;

  return (
    <main className="reading">
      <section className="hero">
        <h1>{t("pr.titulo", { proyecto: project })}</h1>
        <p className="lead">{t("pr.lead")}</p>
      </section>

      {vista.managed ? (
        vista.prompts.map((prompt) => (
          <Ficha
            key={prompt.id}
            hallazgo={hallazgos.find((f) => f.id.startsWith(`prompt_caro:${prompt.name}:v`))}
            prompt={prompt}
            project={project}
            days={days}
            query={query}
            onChange={() => recargar(project)}
          />
        ))
      ) : (
        <SinAdoptar project={project} />
      )}

      <Observados vista={vista} query={query} managed={vista.managed} />

      <Nuevo project={project} onChange={() => recargar(project)} />
    </main>
  );
}

// ---------------------------------------------------------------------------------
// Un prompt gestionado
// ---------------------------------------------------------------------------------

function Ficha({
  prompt,
  hallazgo,
  project,
  days,
  query,
  onChange,
}: {
  prompt: PromptCard;
  hallazgo?: Finding;
  project: string;
  days: number;
  query: string;
  onChange: () => void;
}) {
  const { escribir } = usePermisos(project);
  const [detalle, setDetalle] = useState<PromptCard | null>(null);
  const [abierto, setAbierto] = useState(false);
  const [error, setError] = useState("");
  const [aviso, setAviso] = useState("");

  async function abrir() {
    if (abierto) return setAbierto(false);
    setAbierto(true);
    if (detalle) return;
    try {
      setDetalle(await getPrompt(prompt.id, project, days));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("pr.error.abrir"));
    }
  }

  async function desplegar(version: number) {
    setError("");
    try {
      const resultado = await setProduction(prompt.id, { project_id: project, version });
      setAviso(resultado.detail);
      setDetalle(await getPrompt(prompt.id, project, days));
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("pr.error.version"));
    }
  }

  const ficha = detalle ?? prompt;
  const veredicto = ficha.comparison ? tono(ficha.comparison.by_source) : "sin-base";

  return (
    <section className="sec prompt">
      <header className="prompt-top">
        <div>
          <h2>
            {ficha.name}
            {ficha.production_version !== null && (
              <span className="chip prod">{t("pr.en_produccion", { n: ficha.production_version })}</span>
            )}
          </h2>
          {ficha.description && <p className="lead">{ficha.description}</p>}
        </div>
        <button type="button" className="btn" onClick={abrir}>
          {abierto ? t("pr.cerrar") : t("pr.ver_versiones")}
        </button>
      </header>

      {hallazgo && (
        <p className="prompt-hallazgo">
          {t("pr.hallazgo", { titulo: hallazgo.title })}{" "}
          <Link href={`/problema${query}&id=${encodeURIComponent(hallazgo.id)}`}>
            {t("pr.hallazgo.ver")}
          </Link>
        </p>
      )}

      {ficha.comparison ? (
        <div className={`verdict ${veredicto}`}>
          <b>{ficha.comparison.headline}</b>
          <p>{ficha.comparison.detail}</p>
        </div>
      ) : (
        <p className="disclaimer">{ficha.comparison_unavailable}</p>
      )}

      {ficha.fallback_traces > 0 && (
        <div className="caveats">
          <p>
            <strong>
              {tn("pr.reserva", ficha.fallback_traces, { n: number(ficha.fallback_traces) })}
            </strong>{" "}
            {t("pr.reserva.texto")}
          </p>
        </div>
      )}

      <Versiones versiones={ficha.versions} onDeploy={escribir ? desplegar : undefined} />

      <p className="disclaimer">
        {tr("pr.acierto.nota", { enteras: <strong>{t("pr.acierto.enteras")}</strong> })}
      </p>

      {aviso && <p className="okline">{aviso}</p>}
      {error && <p className="verr">{error}</p>}

      {abierto && detalle && (
        <Detalle prompt={detalle} project={project} query={query} onChange={onChange} />
      )}

      <div className="techline pro">
        <span>
          id <b>{ficha.id}</b>
        </span>
        <span>
          {t("pr.tec.versiones")} <b>{ficha.version_count}</b>
        </span>
        <span>
          {t("pr.tec.creado")} <b>{timestamp(ficha.created_at)}</b>
        </span>
      </div>
    </section>
  );
}

/** El color del bloque: manda el que avisa de que empeora. */
function tono(porFuente: { verdict: string }[]): string {
  const conBase = porFuente.filter((c) => c.verdict !== "sin-base");
  if (conBase.some((c) => c.verdict === "peor")) return "peor";
  if (conBase.some((c) => c.verdict === "mejor")) return "mejor";
  return conBase.length > 0 ? "empate" : "sin-base";
}

function Versiones({
  versiones,
  onDeploy,
}: {
  versiones: VersionMetrics[];
  /** Sin él no se ofrece desplegar: quien mira es lector (D-127). */
  onDeploy?: (version: number) => void;
}) {
  // Las versiones sin tráfico se pliegan. En un prompt que lleva un año tocándose hay
  // treinta borradores que no corrieron nunca, y una tabla de treinta filas vacías
  // entierra justo las dos que tienen cifras, que son las que se han abierto a mirar.
  // No se esconden: se cuentan y se despliegan, porque a una vieja se vuelve.
  const [todas, setTodas] = useState(false);
  const conCifras = versiones.filter((v) => v.traces > 0 || v.in_production);
  const mudas = versiones.filter((v) => !conCifras.includes(v));
  const visibles = todas ? versiones : conCifras;

  return (
    <>
    {/* La tabla de versiones tiene ocho columnas en modo avanzado y no cabe en una
        ventana estrecha. Scrollea ella, no la página: una página que se mueve en
        horizontal se siente rota aunque no lo esté. */}
    <div className="ancha">
    <table className="tbl vers">
      <thead>
        <tr>
          <th>{t("pr.col.version")}</th>
          <th className="r">{t("pr.col.por_ejecucion")}</th>
          <th className="r">{t("ev.col.personas")}</th>
          <th className="r">{t("ev.col.juez")}</th>
          <th className="r">{t("pr.col.ejecuciones")}</th>
          <th className="r pro">{t("pr.col.tokens")}</th>
          <th className="r hide-sm">{t("pr.col.ultimo")}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {visibles.map((v) => (
          <tr key={v.label} className={v.in_production ? "enprod" : undefined}>
            <td>
              <b>{v.label}</b>
              {v.in_production && <span className="chip prod mini">{t("pr.produccion")}</span>}
              {v.notes && <div className="meta">{v.notes}</div>}
              {v.created_at && <div className="meta pro">{timestamp(v.created_at)}</div>}
            </td>
            <td className="r money">
              {v.cost_per_execution_usd === null ? (
                <span title={v.cost_unavailable} style={{ color: "var(--ink-3)" }}>
                  —
                </span>
              ) : (
                `${v.cost_is_floor ? "≥ " : ""}${money(v.cost_per_execution_usd)}`
              )}
            </td>
            <td className="r">
              <Celda rate={v.rates.find((r) => r.source === "human")} />
            </td>
            <td className="r">
              <Celda rate={v.rates.find((r) => r.source === "llm_judge")} />
            </td>
            <td className="r">{number(v.traces)}</td>
            <td className="r pro">
              {v.tokens_per_execution === null ? "—" : tokens(v.tokens_per_execution)}
            </td>
            <td className="r hide-sm">{v.last_seen ? timestamp(v.last_seen) : "—"}</td>
            <td className="r">
              {/* La reserva no es una versión guardada: no se puede desplegar. */}
              {v.version > 0 && !v.in_production && (
                onDeploy && <button type="button" className="vbtn ghost" onClick={() => onDeploy(v.version)}>
                  {t("pr.poner")}
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
    {mudas.length > 0 && (
      <button type="button" className="vbtn ghost" onClick={() => setTodas(!todas)}>
        {todas ? t("pr.ocultar_mudas") : tn("pr.ver_mudas", mudas.length)}
      </button>
    )}
    </>
  );
}

/**
 * Una tasa de acierto con su guarda (D-087), igual que en Evaluaciones.
 *
 * Por debajo del mínimo de casos se enseñan los casos en bruto y el motivo, nunca un
 * porcentaje. En modo avanzado, el margen de Wilson.
 */
function Celda({ rate }: { rate?: Rate }) {
  if (!rate || rate.judged === 0) return <span style={{ color: "var(--ink-3)" }}>—</span>;
  if (rate.value === null)
    return (
      <span title={rate.unavailable}>
        {rate.passed}/{rate.judged}
      </span>
    );
  return (
    <>
      <span>{porcentaje(rate.value)}</span>
      <div className="meta pro">
        {t("pr.wilson", { bajo: porcentaje(rate.low!), alto: porcentaje(rate.high!) })}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------------
// El detalle: texto, diff e historial
// ---------------------------------------------------------------------------------

function Nuevo({ project, onChange }: { project: string; onChange: () => void }) {
  const [nombre, setNombre] = useState("");
  const [texto, setTexto] = useState("");
  const [error, setError] = useState("");
  const [creando, setCreando] = useState(false);

  async function crear() {
    if (!nombre.trim() || !texto.trim()) return;
    setCreando(true);
    setError("");
    try {
      await createPrompt({ project_id: project, name: nombre.trim(), text: texto });
      setNombre("");
      setTexto("");
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("seg.error.crear"));
    } finally {
      setCreando(false);
    }
  }

  return (
    <section className="sec">
      <h2>{t("pr.nuevo")}</h2>
      <p className="lead">
        {tr("pr.nuevo.lead", { codigo: <code>laplace.get_prompt(&quot;…&quot;)</code> })}
      </p>
      <div className="ab">
        <label className="grow">
          <small>{t("pr.nombre")}</small>
          <input
            className="field"
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder="resumen"
          />
        </label>
      </div>
      <textarea
        className="field"
        rows={6}
        value={texto}
        placeholder={t("pr.nuevo.placeholder")}
        onChange={(e) => setTexto(e.target.value)}
      />
      <div className="actions">
        <button
          type="button"
          className="btn primary"
          onClick={crear}
          disabled={creando || !nombre.trim() || !texto.trim()}
        >
          {creando ? t("conj.creando") : t("pr.crear")}
        </button>
      </div>
      {error && <p className="verr">{error}</p>}
    </section>
  );
}

export default function PromptsPage() {
  return (
    <Suspense fallback={<Cargando />}>
      <Contenido />
    </Suspense>
  );
}
