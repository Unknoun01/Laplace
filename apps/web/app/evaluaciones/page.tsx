"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { BackendDown, Cargando, NeedsKey, NoProject, NotYours } from "@/components/states";
import {
  ApiError,
  compareRuns,
  createDataset,
  deleteDataset,
  judgeStatus,
  listDatasets,
  listProjects,
  listRuns,
  parseDays,
  runJudge,
} from "@/lib/api";
import { usePermisos } from "@/lib/permisos";
import { duration, money, number, timestamp, tokens } from "@/lib/format";
import type {
  Comparison,
  Dataset,
  JudgeStatus,
  Rate,
  RunSummary,
  SourceComparison,
  VariantSide,
} from "@/lib/types";

/**
 * Evaluaciones: «¿mi agente responde bien?», frente a Diagnóstico, que responde a
 * «¿cuesta lo que debe?».
 *
 * El orden de la pantalla es el orden de lo que vale: **A vs B primero**, porque es lo
 * que convierte a Laplace en algo que se abre antes de desplegar y no sólo cuando algo
 * ya ha fallado. Los conjuntos y las tiradas van debajo, que es material de apoyo.
 *
 * Dos cosas que no se negocian aquí: ningún porcentaje sin su guarda (D-087), y el
 * veredicto de las personas y el del juez en bloques separados, nunca fundidos (D-083).
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
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [juez, setJuez] = useState<JudgeStatus | null>(null);

  const recargar = useCallback(
    async (id: string) => {
      const [ds, rs, jz] = await Promise.all([
        listDatasets(id),
        listRuns(id),
        judgeStatus().catch(() => null),
      ]);
      setDatasets(ds);
      setRuns(rs);
      setJuez(jz);
    },
    [],
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

  const query = `?project=${encodeURIComponent(project)}&days=${days}`;

  return (
    <main className="reading">
      <section className="hero">
        <h1>¿Tu agente «{project}» responde bien?</h1>
        <p className="lead">
          Diagnóstico te dice si cuesta lo que debe. Esto te dice si acierta, y sobre todo
          si la versión nueva acierta igual y cuesta menos, que es lo que se mira antes de
          desplegar y no cuando algo ya ha fallado.
        </p>
      </section>

      <Experimento
        project={project}
        runs={runs}
        datasets={datasets}
        juez={juez}
        query={query}
        onJudged={() => recargar(project)}
      />

      <Conjuntos
        project={project}
        datasets={datasets}
        query={query}
        onChange={() => recargar(project)}
      />

      <Tiradas runs={runs} query={query} project={project} />
    </main>
  );
}

// ---------------------------------------------------------------------------------
// A vs B: lo valioso
// ---------------------------------------------------------------------------------

function Experimento({
  project,
  runs,
  datasets,
  juez,
  query,
  onJudged,
}: {
  project: string;
  runs: RunSummary[];
  datasets: Dataset[];
  juez: JudgeStatus | null;
  query: string;
  onJudged: () => void;
}) {
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [comp, setComp] = useState<Comparison | null>(null);
  const [error, setError] = useState("");
  const [cargando, setCargando] = useState(false);

  // Sólo tiene sentido comparar tiradas del mismo conjunto, así que al elegir A se
  // filtran las candidatas a B en vez de dejar que el backend rechace la pareja.
  const elegidaA = runs.find((r) => r.run_id === a);
  const candidatasB = runs.filter(
    (r) => r.run_id !== a && (!elegidaA || r.dataset_id === elegidaA.dataset_id),
  );

  async function comparar() {
    if (!a || !b) return;
    setCargando(true);
    setError("");
    try {
      setComp(await compareRuns(project, a, b));
    } catch (e) {
      setComp(null);
      setError(e instanceof Error ? e.message : "no se ha podido comparar");
    } finally {
      setCargando(false);
    }
  }

  if (runs.length < 2) {
    // Tres pasos que se marcan solos al cumplirse, en vez de tres párrafos que había que
    // leer para saber por dónde empezar (D-125).
    const conjunto = datasets[0]?.name ?? "mi-conjunto";
    const pasos = [
      {
        hecho: datasets.length > 0,
        titulo: "Guarda un conjunto de casos",
        cuerpo: (
          <>
            Ejecuciones reales de tu agente. Desde <Link href={`/trazas${query}`}>el
            explorador</Link>, filtra y pulsa «Guardar estas trazas como conjunto», o créalo
            aquí abajo con las más recientes.
          </>
        ),
      },
      {
        hecho: runs.length >= 1,
        titulo: "Lánzalo con la versión actual",
        cuerpo: (
          <>
            La tirada la lanza el SDK en tu proceso —Laplace no ejecuta tu agente—:
            <pre>{`import laplace
laplace.init(project="${project}")
laplace.run_dataset("${conjunto}", mi_agente, variant="actual")`}</pre>
          </>
        ),
      },
      {
        hecho: runs.length >= 2,
        titulo: "Y otra vez con la versión nueva",
        cuerpo: (
          <>
            Otro prompt, otro modelo: la misma línea con otro <code>variant</code>. Con las
            dos tiradas, aquí aparece la comparación de acierto y coste. El acierto sale de
            tus anotaciones o del juez automático.
          </>
        ),
      },
    ];
    const siguiente = pasos.findIndex((p) => !p.hecho);
    return (
      <section className="sec">
        <h2>Comparar dos versiones</h2>
        <p className="lead">Tres pasos, y cada uno se marca solo cuando está hecho.</p>
        <ol className="pasos">
          {pasos.map((p, i) => (
            <li
              key={p.titulo}
              className={p.hecho ? "hecho" : i === siguiente ? "siguiente" : "pendiente"}
            >
              <b>{p.titulo}</b>
              {i === siguiente && <div className="cuerpo">{p.cuerpo}</div>}
            </li>
          ))}
        </ol>
      </section>
    );
  }

  return (
    <section className="sec">
      <h2>Comparar dos versiones</h2>
      <p className="lead">
        Acierto y coste a la vez. Las dos tiradas tienen que ser del mismo conjunto:
        comparar el acierto sobre casos distintos no diría nada.
      </p>

      <div className="ab">
        <label>
          <small>A — la de referencia</small>
          <select
            className="field"
            value={a}
            onChange={(e) => {
              setA(e.target.value);
              setB("");
              setComp(null);
            }}
          >
            <option value="">Elige una tirada</option>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.variant} · {r.dataset_name || r.dataset_id} · {r.cases} casos
              </option>
            ))}
          </select>
        </label>
        <label>
          <small>B — la nueva</small>
          <select
            className="field"
            value={b}
            onChange={(e) => {
              setB(e.target.value);
              setComp(null);
            }}
            disabled={!a}
          >
            <option value="">{a ? "Elige una tirada" : "Elige A primero"}</option>
            {candidatasB.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.variant} · {r.cases} casos
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn primary" onClick={comparar} disabled={!a || !b || cargando}>
          {cargando ? "Comparando…" : "Comparar"}
        </button>
      </div>

      {error && <p className="verr">{error}</p>}

      {comp && (
        <>
          <div className={`verdict ${principal(comp)}`}>
            <b>{comp.headline}</b>
            <p>{comp.detail}</p>
          </div>

          {comp.sources_disagree && (
            <div className="caveats">
              <p>
                <strong>Las personas y el juez no dicen lo mismo.</strong> No es un fallo
                del producto: es el dato más interesante que puede darte esta pantalla. O
                el juez está midiendo otra cosa, o quien anotó y quien escribió el prompt
                del juez no entienden igual «bien». Mira los casos antes de fiarte de
                ninguno de los dos.
              </p>
            </div>
          )}

          <div className="ab-grid">
            {comp.by_source.map((c) => (
              <BloqueFuente key={c.source} comparacion={c} />
            ))}
          </div>

          <h3 className="sub">Lo que costó cada una</h3>
          <p className="lead">
            Esto no lleva margen: no es una muestra, es la factura de lo que se ejecutó.
            Si gestionas tus prompts en Laplace, debajo de cada lado verás{" "}
            <Link href={`/prompts?project=${encodeURIComponent(project)}`}>
              con qué versión corrió
            </Link>
            , leída de las trazas y no de la etiqueta que le pusiste a la tirada.
          </p>
          <div className="ab-grid">
            <Coste lado={comp.a} etiqueta="A" />
            <Coste lado={comp.b} etiqueta="B" />
          </div>

          {juez?.enabled && (
            <div className="actions">
              <button
                type="button"
                className="btn"
                onClick={async () => {
                  await runJudge({ project_id: project, run_id: comp.a.run_id });
                  await runJudge({ project_id: project, run_id: comp.b.run_id });
                  onJudged();
                  await comparar();
                }}
              >
                Juzgar las dos tiradas con {juez.model}
              </button>
              <span className="chip where">
                Cuesta dinero: se te dirá cuánto en cuanto termine
              </span>
            </div>
          )}

          <div className="techline pro">
            <span>
              A <b>{comp.a.run_id}</b>
            </span>
            <span>
              B <b>{comp.b.run_id}</b>
            </span>
            <span>
              conjunto <b>{comp.dataset_id}</b>
            </span>
            <span>
              casos <b>{comp.cases}</b>
            </span>
          </div>
        </>
      )}
    </section>
  );
}

/** El veredicto que manda en el titular, para darle color al bloque. */
function principal(comp: Comparison): string {
  const conBase = comp.by_source.filter((c) => c.verdict !== "sin-base");
  if (conBase.some((c) => c.verdict === "peor")) return "peor";
  if (conBase.some((c) => c.verdict === "mejor")) return "mejor";
  return conBase.length > 0 ? "empate" : "sin-base";
}

function BloqueFuente({ comparacion }: { comparacion: SourceComparison }) {
  const etiqueta = comparacion.source === "human" ? "Personas" : "Juez (modelo)";
  return (
    <div className={`fuente ${comparacion.source} ${comparacion.verdict}`}>
      <header>
        <span className="ftag">{etiqueta}</span>
        <b>{comparacion.headline}</b>
      </header>
      <div className="frates">
        <Acierto rate={comparacion.a} etiqueta="A" />
        <Acierto rate={comparacion.b} etiqueta="B" />
      </div>
      <p>{comparacion.detail}</p>
    </div>
  );
}

/**
 * Un acierto con su guarda.
 *
 * Cuando no hay casos suficientes se enseñan los casos en bruto y el motivo, nunca un
 * porcentaje: es el mismo patrón del 468 $/mes y del 193.100 %, y ya mordió tres veces.
 */
function Acierto({ rate, etiqueta }: { rate: Rate; etiqueta: string }) {
  return (
    <div className="acierto">
      <small>{etiqueta}</small>
      {rate.value === null ? (
        <>
          <b className="num none">
            {rate.passed}/{rate.judged || "—"}
          </b>
          <span className="why">{rate.unavailable}</span>
        </>
      ) : (
        <>
          <b className="num">{(rate.value * 100).toFixed(0)} %</b>
          <span className="why">
            {rate.passed} de {rate.judged}
            {rate.unjudged > 0 && ` · ${rate.unjudged} sin anotar`}
          </span>
          <span className="why pro">
            entre {(rate.low! * 100).toFixed(0)} % y {(rate.high! * 100).toFixed(0)} %
            (Wilson, 95 %)
          </span>
        </>
      )}
    </div>
  );
}

function Coste({ lado, etiqueta }: { lado: VariantSide; etiqueta: string }) {
  const suelo = lado.cost_is_floor ? "≥ " : "";
  return (
    <div className="mcard">
      <small>
        {etiqueta} · {lado.variant}
      </small>
      <b className="num">
        {suelo}
        {lado.cost_per_case_usd === null ? "—" : money(lado.cost_per_case_usd)}
      </b>
      <span className="why">por caso</span>
      {/* Con qué prompt corrió este lado. Sale de las trazas y no de `variant`, que es
          lo que alguien tecleó y la mitad de las veces se queda sin actualizar (D-094). */}
      {lado.prompt_versions.length > 0 && (
        <div className="pvers">
          {lado.prompt_versions.map((v) => (
            <span key={v} className="chip where">
              {v}
            </span>
          ))}
        </div>
      )}
      <div className="techline pro" style={{ marginTop: 8 }}>
        <span>
          total <b>{money(lado.cost_usd)}</b>
        </span>
        <span>
          tokens <b>{tokens(lado.input_tokens + lado.output_tokens)}</b>
        </span>
        {lado.duration_ms_per_case !== null && (
          <span>
            duración <b>{duration(lado.duration_ms_per_case)}</b>
          </span>
        )}
        {lado.crashed > 0 && (
          <span>
            reventados <b>{lado.crashed}</b>
          </span>
        )}
      </div>
      {lado.judge_cost_usd > 0 && (
        <p className="jcost">
          Juzgar esta tirada costó <b>{money(lado.judge_cost_usd)}</b> aparte
          {lado.judge_cost_unknown && " (y hay veredictos cuyo coste no sabemos)"}. Es
          gasto nuestro, no de tu agente, y por eso va separado.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------
// Conjuntos de casos
// ---------------------------------------------------------------------------------

function Conjuntos({
  project,
  datasets,
  query,
  onChange,
}: {
  project: string;
  datasets: Dataset[];
  query: string;
  onChange: () => void;
}) {
  const [nombre, setNombre] = useState("");
  const [limite, setLimite] = useState(50);
  const [error, setError] = useState("");
  const [creando, setCreando] = useState(false);

  async function crear() {
    if (!nombre.trim()) return;
    setCreando(true);
    setError("");
    try {
      await createDataset({
        project_id: project,
        name: nombre.trim(),
        filter: { sort: "recent" },
        limit: limite,
      });
      setNombre("");
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "no se ha podido crear");
    } finally {
      setCreando(false);
    }
  }

  const { escribir } = usePermisos(project);
  return (
    <section className="sec">
      <fieldset className="sin-marco" disabled={!escribir}>
      <h2>Conjuntos de casos</h2>
      <p className="lead">
        Colecciones de ejecuciones <strong>reales</strong> de tu agente. No hay casos
        inventados aquí: cada uno sale de una traza que ocurrió, y se puede abrir para ver
        de dónde salió.
      </p>

      <div className="ab">
        <label className="grow">
          <small>Nombre</small>
          <input
            className="field"
            value={nombre}
            onChange={(e) => setNombre(e.target.value)}
            placeholder="regresiones-checkout"
          />
        </label>
        <label>
          <small>Cuántas trazas</small>
          <input
            className="field"
            type="number"
            min={1}
            max={500}
            value={limite}
            onChange={(e) => setLimite(Number(e.target.value) || 50)}
            style={{ width: 110 }}
          />
        </label>
        <button type="button" className="btn" onClick={crear} disabled={creando || !nombre.trim()}>
          {creando ? "Creando…" : "Crear desde las más recientes"}
        </button>
      </div>
      <p className="disclaimer">
        ¿Quieres otro filtro? Fíltralo en{" "}
        <Link href={`/trazas${query}`}>el explorador</Link> y pulsa «Guardar estas trazas
        como conjunto de casos»: el conjunto guarda el filtro con el que se formó, para
        que después se pueda discutir de dónde salió.
      </p>
      {error && <p className="verr">{error}</p>}

      {datasets.length === 0 ? (
        <p className="disclaimer">Todavía no hay ninguno.</p>
      ) : (
        <div className="tbl-scroll">
          <table className="tbl">
            <thead>
              <tr>
                <th>Conjunto</th>
                <th className="r">Casos</th>
                <th className="pro">Filtro de origen</th>
                <th className="r hide-sm">Creado</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {datasets.map((d) => (
                <tr key={d.id}>
                  <td>
                    {d.name}
                    <div className="meta pro">{d.id}</div>
                  </td>
                  <td className="r">{number(d.item_count)}</td>
                  <td className="pro" style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                    {Object.entries(d.source_filter).length === 0
                      ? "sin filtro (las más recientes)"
                      : Object.entries(d.source_filter)
                          .map(([k, v]) => `${k}=${v}`)
                          .join(" · ")}
                  </td>
                  <td className="r hide-sm">{timestamp(d.created_at)}</td>
                  <td className="r">
                    <button
                      type="button"
                      className="vbtn ghost"
                      onClick={async () => {
                        await deleteDataset(d.id, project);
                        onChange();
                      }}
                    >
                      Borrar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      </fieldset>
    </section>
  );
}

// ---------------------------------------------------------------------------------
// Tiradas
// ---------------------------------------------------------------------------------

function Tiradas({
  runs,
  query,
  project,
}: {
  runs: RunSummary[];
  query: string;
  project: string;
}) {
  if (runs.length === 0) return null;
  return (
    <section className="sec">
      <h2>Tiradas</h2>
      <p className="lead">
        Cada una es una pasada de un conjunto por una versión de tu agente. Las lanza el
        SDK en tu proceso; aquí sólo llega el parte y las trazas.
      </p>
      <div className="tbl-scroll">
        <table className="tbl">
          <thead>
            <tr>
              <th>Versión</th>
              <th>Conjunto</th>
              <th className="r">Casos</th>
              <th className="r">Personas</th>
              <th className="r">Juez</th>
              <th className="r">Coste</th>
              <th className="r pro">Juzgar costó</th>
              <th className="r hide-sm">Cuándo</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.run_id}>
                <td>
                  {r.variant}
                  {/* La versión de prompt con la que corrió de verdad, por si `variant`
                      se quedó viejo, que es lo que pasa la mitad de las veces. */}
                  {r.prompt_versions.length > 0 && (
                    <div className="meta">{r.prompt_versions.join(" · ")}</div>
                  )}
                  <div className="meta pro">{r.run_id}</div>
                </td>
                <td>{r.dataset_name || r.dataset_id}</td>
                <td className="r">{r.cases}</td>
                <td className="r">
                  <Celda rate={r.rates.find((x) => x.source === "human")} />
                </td>
                <td className="r">
                  <Celda rate={r.rates.find((x) => x.source === "llm_judge")} />
                </td>
                <td className="r money">{money(r.cost_usd)}</td>
                <td className="r money pro">
                  {r.judge_cost_usd > 0 ? money(r.judge_cost_usd) : "—"}
                </td>
                <td className="r hide-sm">{timestamp(r.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="disclaimer">
        ¿Sin tiradas de una versión nueva? Lánzala con{" "}
        <code>laplace.run_dataset(&quot;…&quot;, mi_agente, variant=&quot;…&quot;)</code> apuntando
        a este mismo Laplace. Las trazas que genere se ven en{" "}
        <Link href={`/trazas${query}`}>el explorador</Link> como cualquier otra, y el
        proyecto es «{project}».
      </p>
    </section>
  );
}

/** Una celda de acierto: porcentaje si lo hay, casos en bruto si no. */
function Celda({ rate }: { rate?: Rate }) {
  if (!rate || rate.judged === 0) return <span style={{ color: "var(--ink-3)" }}>—</span>;
  if (rate.value === null)
    return (
      <span title={rate.unavailable}>
        {rate.passed}/{rate.judged}
      </span>
    );
  return (
    <span title={`entre ${(rate.low! * 100).toFixed(0)} % y ${(rate.high! * 100).toFixed(0)} %`}>
      {(rate.value * 100).toFixed(0)} %
    </span>
  );
}

export default function EvaluacionesPage() {
  return (
    <Suspense fallback={<Cargando />}>
      <Contenido />
    </Suspense>
  );
}
