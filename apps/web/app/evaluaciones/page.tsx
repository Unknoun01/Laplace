"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { BackendDown, Cargando, NeedsKey, NoProject, NotYours } from "@/components/states";
import { ApiError, getOverview, judgeStatus, listDatasets, listProjects, listRuns, parseDays } from "@/lib/api";
import { usePermisos } from "@/lib/permisos";
import type { Dataset, Finding, JudgeStatus, RunSummary } from "@/lib/types";
import { Conjuntos, Tiradas } from "./conjuntos";
import { Experimento } from "./experimento";
import { PorProbar } from "./porprobar";
import { t } from "@/lib/textos";

/**
 * Probar (antes Evaluaciones): el segundo paso del ciclo detectar → probar → arreglar →
 * verificar (D-156). Diagnóstico responde a «¿cuesta lo que debe?»; esto, a «¿el
 * arreglo acierta igual?». Arriba, los arreglos que el Diagnóstico propone y si ya
 * tienen su prueba; debajo, lo de siempre.
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
  const [codigo, setCodigo] = useState("");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [juez, setJuez] = useState<JudgeStatus | null>(null);
  const [hallazgos, setHallazgos] = useState<Finding[]>([]);
  const permisos = usePermisos(project);

  const recargar = useCallback(
    async (id: string) => {
      const [ds, rs, jz, vista] = await Promise.all([
        listDatasets(id),
        listRuns(id),
        judgeStatus().catch(() => null),
        // Lo que hay por probar sale del Diagnóstico. Si falla, la pantalla sigue en
        // pie con lo de siempre: no es lo único que hay aquí.
        getOverview(id, days).catch(() => null),
      ]);
      setDatasets(ds);
      setRuns(rs);
      setJuez(jz);
      setHallazgos(vista?.findings ?? []);
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

  const query = `?project=${encodeURIComponent(project)}&days=${days}`;

  return (
    <main className="reading">
      <section className="hero">
        <h1>{t("ev.titulo", { proyecto: project })}</h1>
        <p className="lead">{t("ev.lead")}</p>
      </section>

      <PorProbar
        project={project}
        findings={hallazgos}
        datasets={datasets}
        runs={runs}
        escribir={permisos.escribir}
        query={query}
        onChange={() => recargar(project)}
      />

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

export default function EvaluacionesPage() {
  return (
    <Suspense fallback={<Cargando />}>
      <Contenido />
    </Suspense>
  );
}
