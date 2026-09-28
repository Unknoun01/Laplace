"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { createDataset, listDatasets, listRuns } from "@/lib/api";
import { number, timestamp } from "@/lib/format";
import type { Dataset, FindingDetail, RunSummary } from "@/lib/types";
import { t, tn } from "@/lib/textos";

/**
 * El ciclo de un problema: detectar → probar → arreglar → verificar (D-156).
 *
 * La cifra que vende Laplace es «ahorrado y recuperable», y un problema sólo pasa de
 * lo segundo a lo primero recorriendo los cuatro pasos. La ficha enseñaba cada paso
 * por su lado —el arreglo, el conjunto para probarlo, el botón de marcarlo, el antes y
 * después— sin decir en cuál estaba el problema. Aquí se dice, con lo que ya se sabe:
 * los conjuntos guardados con las llamadas de su paso, sus tiradas, y lo que el
 * seguimiento ha medido después de marcarlo. Nada se infiere: un paso sin datos que lo
 * den por hecho está pendiente.
 */

type EstadoPaso = "hecho" | "en_curso" | "pendiente" | "fallo";

interface Paso {
  nombre: string;
  estado: EstadoPaso;
  texto: string;
}

/** Los conjuntos guardados con las llamadas de este paso, y sus tiradas. */
export interface Pruebas {
  conjunto: Dataset | null;
  tiradas: RunSummary[];
}

/**
 * El conjunto que cuenta como «la prueba» de un paso: de los que se guardaron con su
 * filtro, el que más tiradas tiene, y a igualdad el más reciente.
 */
export function pruebasDelPaso(
  stepKey: string,
  conjuntos: Dataset[],
  tiradas: RunSummary[],
): Pruebas {
  if (!stepKey) return { conjunto: null, tiradas: [] };
  const suyos = conjuntos.filter((d) => d.source_filter?.step_key === stepKey);
  const conTiradas = suyos.map((d) => ({
    d,
    tiradas: tiradas.filter((r) => r.dataset_id === d.id),
  }));
  conTiradas.sort(
    (a, b) =>
      b.tiradas.length - a.tiradas.length || b.d.created_at.localeCompare(a.d.created_at),
  );
  const elegido = conTiradas[0];
  return elegido ? { conjunto: elegido.d, tiradas: elegido.tiradas } : { conjunto: null, tiradas: [] };
}

export function pasosDelCiclo(finding: FindingDetail, pruebas: Pruebas | null): Paso[] {
  const probar: Paso = { nombre: t("ciclo.probar"), estado: "pendiente", texto: "" };
  if (!finding.step_key) {
    probar.texto = t("ciclo.probar.sin_paso");
  } else if (pruebas?.conjunto && pruebas.tiradas.length > 0) {
    probar.estado = "hecho";
    probar.texto = tn("ciclo.probar.hecho", pruebas.tiradas.length, {
      nombre: pruebas.conjunto.name,
    });
  } else if (pruebas?.conjunto) {
    probar.estado = "en_curso";
    probar.texto = t("ciclo.probar.conjunto", { nombre: pruebas.conjunto.name });
  } else {
    probar.texto = t("ciclo.probar.pendiente");
  }

  const arreglar: Paso = {
    nombre: t("ciclo.arreglar"),
    estado: "pendiente",
    texto: t("ciclo.arreglar.pendiente"),
  };
  const verificar: Paso = {
    nombre: t("ciclo.verificar"),
    estado: "pendiente",
    texto: t("ciclo.verificar.pendiente"),
  };
  const check = finding.fix_check;
  if (finding.state === "ignorado") {
    arreglar.texto = t("ciclo.ignorado");
    verificar.texto = "";
  } else if (finding.state === "arreglado" || finding.state === "reaparecido") {
    arreglar.estado = "hecho";
    arreglar.texto = finding.state_at
      ? t("ciclo.arreglar.hecho", { fecha: timestamp(finding.state_at) })
      : "";
    if (check) {
      verificar.texto = check.headline;
      verificar.estado =
        check.verdict === "arreglado"
          ? "hecho"
          : check.verdict === "sigue"
            ? "fallo"
            : "en_curso";
    }
  }

  return [
    { nombre: t("ciclo.detectar"), estado: "hecho", texto: t("ciclo.detectar.hecho") },
    probar,
    arreglar,
    verificar,
  ];
}

const ETIQUETA = {
  hecho: "ciclo.estado.hecho",
  en_curso: "ciclo.estado.en_curso",
  pendiente: "ciclo.estado.pendiente",
  fallo: "ciclo.estado.fallo",
} as const satisfies Record<EstadoPaso, string>;

/**
 * El ciclo en la ficha y, para quien puede escribir, la prueba: guardar las llamadas
 * reales del paso como conjunto y la línea para lanzarlas con el arreglo puesto.
 */
export function Ciclo({
  project,
  finding,
  query,
  escribir,
}: {
  project: string;
  finding: FindingDetail;
  query: string;
  escribir: boolean;
}) {
  const [pruebas, setPruebas] = useState<Pruebas | null>(null);

  const cargar = useCallback(async () => {
    try {
      const [conjuntos, tiradas] = await Promise.all([listDatasets(project), listRuns(project)]);
      setPruebas(pruebasDelPaso(finding.step_key, conjuntos, tiradas));
    } catch {
      // Sin base de metadatos no hay conjuntos: el paso «probar» se queda pendiente,
      // que es lo que se sabe. La ficha no se cae por esto.
      setPruebas({ conjunto: null, tiradas: [] });
    }
  }, [project, finding.step_key]);

  useEffect(() => {
    cargar();
  }, [cargar]);

  const pasos = pasosDelCiclo(finding, pruebas);

  return (
    <>
      <section className="block ciclo" aria-labelledby="ciclo-titulo">
        <h2 id="ciclo-titulo">{t("ciclo.titulo")}</h2>
        <ol className="ciclo-pasos">
          {pasos.map((paso) => (
            <li key={paso.nombre} className={`ciclo-paso ${paso.estado}`}>
              <span className="ciclo-marca" aria-hidden />
              <div>
                <b>{paso.nombre}</b> <span className="sr">({t(ETIQUETA[paso.estado])})</span>
                {paso.texto && <p>{paso.texto}</p>}
              </div>
            </li>
          ))}
        </ol>
      </section>

      {escribir && finding.step_key && pruebas && (
        <Probar
          project={project}
          finding={finding}
          query={query}
          conjunto={pruebas.conjunto}
          onCreado={cargar}
        />
      )}
    </>
  );
}

/**
 * Probar antes de cambiar (D-123, generalizado en D-156). El ahorro está medido; que
 * el agente arreglado acierte igual, no. Vale para cualquier problema de un paso, no
 * sólo para el modelo caro: el conjunto son las llamadas reales de ese paso, y cada
 * tipo de problema dice con qué lanzarlas.
 */
function Probar({
  project,
  finding,
  query,
  conjunto,
  onCreado,
}: {
  project: string;
  finding: FindingDetail;
  query: string;
  conjunto: Dataset | null;
  onCreado: () => void;
}) {
  const [error, setError] = useState("");
  const nombre = conjunto?.name ?? `ab-${finding.step_key.slice(0, 8)}`;
  const version = versionesDelPrompt(finding);

  async function crear() {
    try {
      await createDataset({
        project_id: project,
        name: nombre,
        filter: { step_key: finding.step_key, sort: "recent" },
        limit: 50,
      });
      onCreado();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("seg.error.crear"));
    }
  }

  const texto =
    finding.kind === "modelo_caro"
      ? t("seg.probar.texto", { modelo: alternativa(finding) || t("seg.probar.barato") })
      : finding.kind === "prompt_caro" && version
        ? t("seg.probar.texto.prompt", { a: version.a, b: version.b })
        : t("seg.probar.texto.otro");

  return (
    <section className="block" id="probar">
      <h2>{t("seg.probar.titulo")}</h2>
      <p>{texto}</p>
      {conjunto ? (
        <>
          <p className="muted">
            {t("seg.probar.existe", { nombre: conjunto.name, n: number(conjunto.item_count) })}
          </p>
          <pre>{lineaDePrueba(project, conjunto.name, finding)}</pre>
          <p>
            <Link href={`/evaluaciones${query}`}>{t("seg.probar.ir")}</Link>
          </p>
        </>
      ) : (
        <div className="actions" style={{ paddingTop: 0 }}>
          <button type="button" className="btn" onClick={crear}>
            {t("seg.probar.crear", { nombre })}
          </button>
        </div>
      )}
      {error && <p className="verr">{error}</p>}
    </section>
  );
}

/** El modelo barato que propone el hallazgo: el valor con «→» de su línea técnica. */
function alternativa(finding: FindingDetail): string {
  return (
    finding.tech
      .find((item) => item.value.includes("→") && !/^v\d+ /.test(item.value))
      ?.value.split("→")
      .pop()
      ?.trim() ?? ""
  );
}

/**
 * El prompt y sus dos versiones, de un hallazgo `prompt_caro`: el nombre y la cara
 * salen del id (`prompt_caro:<nombre>:v<n>`), la anterior de la línea técnica
 * «v7 … → v8 …», que el backend escribe siempre con esa forma.
 */
export function versionesDelPrompt(
  finding: FindingDetail,
): { prompt: string; a: number; b: number } | null {
  if (finding.kind !== "prompt_caro") return null;
  const id = /^prompt_caro:(.+):v(\d+)$/.exec(finding.id);
  const par = finding.tech
    .map((item) => /^v(\d+) .*→ v(\d+) /.exec(item.value))
    .find((m) => m !== null);
  if (!id || !par) return null;
  return { prompt: id[1], a: Number(par[1]), b: Number(par[2]) };
}

function lineaDePrueba(project: string, conjunto: string, finding: FindingDetail): string {
  const cabecera = `import laplace\nlaplace.init(project="${project}")\n`;
  const version = versionesDelPrompt(finding);
  if (version) {
    return (
      cabecera +
      `for v in (${version.a}, ${version.b}):\n` +
      `    prompt = laplace.get_prompt("${version.prompt}", version=v)\n` +
      `    laplace.run_dataset("${conjunto}", lambda caso: mi_agente(caso, prompt),\n` +
      `                        variant=f"${version.prompt} v{v}")`
    );
  }
  const variante =
    finding.kind === "modelo_caro"
      ? alternativa(finding) || "modelo-barato"
      : t("seg.probar.arreglado");
  return cabecera + `laplace.run_dataset("${conjunto}", mi_agente, variant="${variante}")`;
}
