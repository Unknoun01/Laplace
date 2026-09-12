"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { BackendDown, Cargando, NeedsKey, NoProject, NotYours } from "@/components/states";
import {
  ApiError,
  addPromptVersion,
  createPrompt,
  deletePrompt,
  getPrompt,
  getPrompts,
  listProjects,
  parseDays,
  promptDiff,
  setProduction,
} from "@/lib/api";
import { money, number, timestamp, tokens } from "@/lib/format";
import type {
  Diff,
  ObservedStep,
  PromptCard,
  PromptsView,
  Rate,
  VersionMetrics,
} from "@/lib/types";

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
  const [vista, setVista] = useState<PromptsView | null>(null);

  const recargar = useCallback(
    async (id: string) => setVista(await getPrompts(id, days)),
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
  if (fase === "sin-clave") return <NeedsKey mensaje={error} />;
  if (fase === "sin-permiso") return <NotYours mensaje={error} />;
  if (fase === "sin-proyecto") return <NoProject />;
  if (!vista) return <Cargando />;

  const query = `?project=${encodeURIComponent(project)}&days=${days}`;

  return (
    <main className="reading">
      <section className="hero">
        <h1>Los prompts de «{project}»</h1>
        <p className="lead">
          Cada versión con lo que costó y lo que acertó sobre el tráfico que la usó. Un
          diff y un botón de rollback los tiene cualquiera; lo que no tiene nadie más es
          poder leer <strong>v8: 0,004 $ por ejecución, 94 %</strong> al lado de{" "}
          <strong>v7: 0,007 $, 93 %</strong> y decidir con eso.
        </p>
      </section>

      {vista.managed ? (
        vista.prompts.map((prompt) => (
          <Ficha
            key={prompt.id}
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
  project,
  days,
  query,
  onChange,
}: {
  prompt: PromptCard;
  project: string;
  days: number;
  query: string;
  onChange: () => void;
}) {
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
      setError(e instanceof Error ? e.message : "no se ha podido abrir");
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
      setError(e instanceof Error ? e.message : "no se ha podido cambiar la versión");
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
              <span className="chip prod">v{ficha.production_version} en producción</span>
            )}
          </h2>
          {ficha.description && <p className="lead">{ficha.description}</p>}
        </div>
        <button type="button" className="btn" onClick={abrir}>
          {abierto ? "Cerrar" : "Ver versiones y diff"}
        </button>
      </header>

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
              {number(ficha.fallback_traces)}{" "}
              {ficha.fallback_traces === 1 ? "ejecución corrió" : "ejecuciones corrieron"} con
              el texto de reserva.
            </strong>{" "}
            Quiere decir que tu agente no pudo pedirle el prompt a Laplace y usó el que
            lleva en el código. Ese tráfico va contado aparte, no dentro de la versión en
            producción: sumarlo ahí falsearía justo la cifra que estás mirando.
          </p>
        </div>
      )}

      <Versiones versiones={ficha.versions} onDeploy={desplegar} />

      <p className="disclaimer">
        El acierto de una versión es el de las <strong>ejecuciones enteras</strong> en las
        que participó, no el de este prompt aislado. Una ejecución pasa por varios pasos y,
        cuando sale mal, nadie ha medido cuál de ellos la estropeó. Es una señal útil, no
        un reparto de culpas.
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
          versiones <b>{ficha.version_count}</b>
        </span>
        <span>
          creado <b>{timestamp(ficha.created_at)}</b>
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
  onDeploy: (version: number) => void;
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
          <th>Versión</th>
          <th className="r">Por ejecución</th>
          <th className="r">Personas</th>
          <th className="r">Juez</th>
          <th className="r">Ejecuciones</th>
          <th className="r pro">Tokens/ejec.</th>
          <th className="r hide-sm">Último uso</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {visibles.map((v) => (
          <tr key={v.label} className={v.in_production ? "enprod" : undefined}>
            <td>
              <b>{v.label}</b>
              {v.in_production && <span className="chip prod mini">producción</span>}
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
                <button type="button" className="vbtn ghost" onClick={() => onDeploy(v.version)}>
                  Poner en producción
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
        {todas
          ? "Ocultar las versiones sin tráfico"
          : `Ver ${mudas.length} ${
              mudas.length === 1 ? "versión que no corrió" : "versiones que no corrieron"
            } en este rango`}
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
      <span>{(rate.value * 100).toFixed(0)} %</span>
      <div className="meta pro">
        {(rate.low! * 100).toFixed(0)}–{(rate.high! * 100).toFixed(0)} % (Wilson)
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------------
// El detalle: texto, diff e historial
// ---------------------------------------------------------------------------------

function Detalle({
  prompt,
  project,
  query,
  onChange,
}: {
  prompt: PromptCard;
  project: string;
  query: string;
  onChange: () => void;
}) {
  const guardadas = prompt.versions.filter((v) => v.version > 0);
  const [b, setB] = useState(guardadas[0]?.version ?? 0);
  const [a, setA] = useState(guardadas[1]?.version ?? guardadas[0]?.version ?? 0);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [texto, setTexto] = useState("");
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let vigente = true;
    if (!a || !b || a === b) {
      setDiff(null);
      return;
    }
    promptDiff(prompt.id, a, b)
      .then((d) => vigente && setDiff(d))
      .catch(() => vigente && setDiff(null));
    return () => {
      vigente = false;
    };
  }, [prompt.id, a, b]);

  const elegida = guardadas.find((v) => v.version === b);

  async function guardar() {
    if (!texto.trim()) return;
    setGuardando(true);
    setError("");
    try {
      await addPromptVersion(prompt.id, { project_id: project, text: texto });
      setTexto("");
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "no se ha podido guardar");
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div className="prompt-det">
      <h3 className="sub">Qué cambió</h3>
      <div className="ab">
        <label>
          <small>De</small>
          <select className="field" value={a} onChange={(e) => setA(Number(e.target.value))}>
            {guardadas.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version}
              </option>
            ))}
          </select>
        </label>
        <label>
          <small>A</small>
          <select className="field" value={b} onChange={(e) => setB(Number(e.target.value))}>
            {guardadas.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version}
              </option>
            ))}
          </select>
        </label>
        {diff && <span className="chip where">{diff.summary}</span>}
      </div>

      {diff ? (
        <div className="diff">
          {diff.lines.map((linea, i) => (
            <div key={i} className={`dl ${linea.op === "+" ? "add" : linea.op === "-" ? "del" : ""}`}>
              <span className="dn pro">{linea.left ?? ""}</span>
              <span className="dn pro">{linea.right ?? ""}</span>
              <span className="dop">{linea.op === "=" ? " " : linea.op}</span>
              <span className="dt">{linea.text || " "}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="disclaimer">
          Elige dos versiones distintas para ver el diff. Con una sola no hay nada que
          comparar.
        </p>
      )}

      {elegida && (
        <>
          <h3 className="sub">El texto de la v{elegida.version}</h3>
          <pre>{elegida.text}</pre>
        </>
      )}

      <h3 className="sub">Guardar una versión nueva</h3>
      <p className="lead">
        Guardar no despliega. Son dos gestos distintos a propósito: es lo que te deja
        preparar una versión con calma y lo que hace que exista el botón de volver atrás.
      </p>
      <textarea
        className="field"
        rows={6}
        value={texto}
        placeholder="El texto del prompt. Usa {{variable}} para lo que cambie en cada llamada."
        onChange={(e) => setTexto(e.target.value)}
      />
      <div className="actions">
        <button type="button" className="btn" onClick={guardar} disabled={guardando || !texto.trim()}>
          {guardando ? "Guardando…" : "Guardar versión"}
        </button>
        <span className="chip where">Queda guardada, no servida</span>
      </div>
      {error && <p className="verr">{error}</p>}

      {prompt.deploys.length > 0 && (
        <div className="pro">
          <h3 className="sub">Historial de despliegues</h3>
          <table className="tbl">
            <thead>
              <tr>
                <th>Cuándo</th>
                <th>Versión</th>
                <th>Quién</th>
                <th>Nota</th>
              </tr>
            </thead>
            <tbody>
              {prompt.deploys.map((d) => (
                <tr key={d.id}>
                  <td>{timestamp(d.at)}</td>
                  <td>
                    v{d.version}
                    {d.rollback && <span className="chip where mini">vuelta atrás</span>}
                  </td>
                  <td>{d.actor || "—"}</td>
                  <td>{d.note || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="disclaimer">
            Este historial cuenta la historia; <strong>no</strong> atribuye picos. Para eso
            el <Link href={`/panel${query}`}>panel</Link> usa la versión que aparece en las
            trazas del tramo, que es un hecho medido y no una coincidencia de horarios.
          </p>
        </div>
      )}

      <div className="actions">
        <button
          type="button"
          className="vbtn ghost"
          onClick={async () => {
            await deletePrompt(prompt.id);
            onChange();
          }}
        >
          Borrar este prompt
        </button>
        <span className="chip where">
          Las trazas no se tocan: seguirán diciendo con qué versión corrieron
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------
// Sin adoptar: la pestaña no se queda en blanco
// ---------------------------------------------------------------------------------

function SinAdoptar({ project }: { project: string }) {
  return (
    <section className="sec">
      <h2>Todavía no gestionas ningún prompt aquí</h2>
      <p className="lead">
        Tus prompts viven en tu código, que es un sitio perfectamente razonable. Lo que se
        pierde con eso es poder responder a «¿cuánto me costaba el de antes y cuánto
        acertaba?», porque un <code>git blame</code> sabe qué cambió y no sabe qué pasó
        después.
      </p>
      <p className="lead">
        Si los mueves aquí, el SDK los sirve y deja escrito en cada traza con qué versión
        se ejecutó. <strong>Laplace sigue sin ejecutar nada tuyo</strong>: sólo te da el
        texto, y si Laplace no responde, el SDK usa la copia guardada o el texto de reserva
        que le pases.
      </p>
      <pre>{`import laplace
laplace.init(project="${project}")

sistema = laplace.get_prompt("resumen", fallback=SISTEMA_DEL_CODIGO)
respuesta = cliente.messages.create(
    model="claude-sonnet-4-5",
    system=sistema.render(idioma="es"),
    messages=[{"role": "user", "content": pregunta}],
)`}</pre>
      <p className="disclaimer">
        Mientras tanto, aquí abajo está lo que sí se puede saber de tus trazas: qué pasos
        han cambiado de instrucciones y cuándo.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------------
// Lo inferido de las trazas
// ---------------------------------------------------------------------------------

function Observados({
  vista,
  query,
  managed,
}: {
  vista: PromptsView;
  query: string;
  managed: boolean;
}) {
  if (vista.observed.length === 0) {
    return (
      <section className="sec">
        <h2>Lo que se ve en tus trazas</h2>
        <p className="lead">{vista.observed_unavailable || "Nada todavía."}</p>
      </section>
    );
  }

  return (
    <section className="sec">
      <h2>Lo que se ve en tus trazas</h2>
      <p className="lead">
        {managed
          ? "Esto sale de las trazas, no de la gestión de prompts: son los juegos de instrucciones con los que se ha visto ejecutar cada paso. Sirve para ver si algo cambió por fuera de aquí."
          : "Cada paso de tu agente, y los juegos de instrucciones con los que se le ha visto ejecutar en este rango. Dos instrucciones distintas bajo el mismo paso son un cambio de prompt: no sabemos el texto entero —sólo guardamos el principio—, pero sí cuándo cambió y qué costó."}
      </p>
      {vista.observed.map((paso) => (
        <PasoObservado key={paso.label} paso={paso} />
      ))}
      <p className="disclaimer">
        ¿Ves un cambio que no reconoces?{" "}
        <Link href={`/trazas${query}`}>Ábrelo en el explorador</Link> y mira las
        instrucciones completas de esas ejecuciones.
      </p>
    </section>
  );
}

function PasoObservado({ paso }: { paso: ObservedStep }) {
  return (
    <article className="card static">
      <div className="card-top">
        <h3>{paso.label}</h3>
        <div className="price">
          {paso.unstable ? "—" : paso.variants.length}
          <small>
            {paso.unstable
              ? "no son versiones"
              : paso.variants.length === 1
                ? "juego de instrucciones"
                : "juegos de instrucciones"}
          </small>
        </div>
      </div>

      {paso.unstable ? (
        <p className="unattributed">{paso.note}</p>
      ) : (
        <div className="ancha">
        <table className="tbl">
          <thead>
            <tr>
              <th>Instrucciones (principio)</th>
              <th className="r">Por ejecución</th>
              <th className="r">Ejecuciones</th>
              <th className="r hide-sm">Desde</th>
              <th className="r hide-sm">Hasta</th>
            </tr>
          </thead>
          <tbody>
            {paso.variants.map((v) => (
              <tr key={v.step_key}>
                <td>
                  <span className="hint">{v.hint || "(sin instrucciones capturadas)"}</span>
                  <div className="meta pro">{v.step_key}</div>
                </td>
                <td className="r money">
                  {v.cost_per_execution_usd === null ? "—" : money(v.cost_per_execution_usd)}
                </td>
                <td className="r">{number(v.traces)}</td>
                <td className="r hide-sm">{v.first_seen ? timestamp(v.first_seen) : "—"}</td>
                <td className="r hide-sm">{v.last_seen ? timestamp(v.last_seen) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </article>
  );
}

// ---------------------------------------------------------------------------------
// Crear
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
      setError(e instanceof Error ? e.message : "no se ha podido crear");
    } finally {
      setCreando(false);
    }
  }

  return (
    <section className="sec">
      <h2>Sacar un prompt del código</h2>
      <p className="lead">
        El nombre es con el que lo pedirás: <code>laplace.get_prompt(&quot;…&quot;)</code>. La
        primera versión se pone en producción sola; a partir de la segunda, guardar y
        desplegar son dos gestos.
      </p>
      <div className="ab">
        <label className="grow">
          <small>Nombre</small>
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
        placeholder="Eres un asistente que responde en {{idioma}} y en menos de tres frases."
        onChange={(e) => setTexto(e.target.value)}
      />
      <div className="actions">
        <button
          type="button"
          className="btn primary"
          onClick={crear}
          disabled={creando || !nombre.trim() || !texto.trim()}
        >
          {creando ? "Creando…" : "Crear y poner en producción"}
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
