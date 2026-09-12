"use client";

import { useState } from "react";
import { annotate, deleteAnnotation } from "@/lib/api";
import { money } from "@/lib/format";
import type { Annotation } from "@/lib/types";

/**
 * Marcar una traza como buena o mala.
 *
 * El botón sólo crea anotaciones **humanas**: el veredicto de máquina entra por la ruta
 * del juez y se pinta aparte, nunca en el mismo control. Si los dos compartieran botón,
 * en dos semanas nadie sabría cuál de los dos dijo qué (D-083).
 */
export function Verdicts({
  projectId,
  traceId,
  annotations,
  onChange,
  compact,
}: {
  projectId: string;
  traceId: string;
  annotations: Annotation[];
  onChange?: (nuevas: Annotation[]) => void;
  /** En el explorador sólo caben los dos botones; en la traza cabe el comentario. */
  compact?: boolean;
}) {
  const humana = annotations.find((a) => a.source === "human");
  const maquina = annotations.find((a) => a.source === "llm_judge");

  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState("");
  const [comentario, setComentario] = useState(humana?.comment ?? "");
  const [abierto, setAbierto] = useState(false);

  async function marcar(verdict: "pass" | "fail") {
    setGuardando(true);
    setError("");
    try {
      // Volver a pulsar el mismo veredicto lo quita: marcar por error no puede ser
      // irreversible, y un botón que sólo suma acaba con todo marcado.
      if (humana?.verdict === verdict) {
        await deleteAnnotation(humana.id);
        onChange?.(annotations.filter((a) => a.id !== humana.id));
      } else {
        const nueva = await annotate({
          project_id: projectId,
          trace_id: traceId,
          verdict,
          comment: comentario || undefined,
        });
        onChange?.([...annotations.filter((a) => a.source !== "human"), nueva]);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "no se ha podido guardar");
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div className={`verdicts${compact ? " compact" : ""}`}>
      <div className="vbtns">
        <button
          type="button"
          className={`vbtn pass${humana?.verdict === "pass" ? " on" : ""}`}
          onClick={() => marcar("pass")}
          disabled={guardando}
          title="Marcar esta ejecución como buena"
        >
          Bien
        </button>
        <button
          type="button"
          className={`vbtn fail${humana?.verdict === "fail" ? " on" : ""}`}
          onClick={() => marcar("fail")}
          disabled={guardando}
          title="Marcar esta ejecución como mala"
        >
          Mal
        </button>
        {!compact && (
          <button type="button" className="vbtn ghost" onClick={() => setAbierto((v) => !v)}>
            {abierto ? "Ocultar comentario" : humana?.comment ? "Ver comentario" : "Comentar"}
          </button>
        )}
        {maquina && <JudgeChip annotation={maquina} />}
      </div>

      {!compact && abierto && (
        <div className="vcomment">
          <textarea
            value={comentario}
            onChange={(e) => setComentario(e.target.value)}
            placeholder="Qué falló, o por qué está bien. Se guarda al marcar."
            rows={2}
          />
          <small>
            El comentario se guarda con el veredicto: vuelve a pulsar «Bien» o «Mal»
            después de escribirlo.
          </small>
        </div>
      )}

      {!compact && maquina?.comment && (
        <p className="jreason">
          <span className="jtag">juez</span> {maquina.comment}
        </p>
      )}
      {/* El error se enseña siempre, también en compacto. Un guardado que falla en
          silencio hace que alguien anote veinte filas y pierda las veinte: el sitio
          donde menos se puede tragar un fallo es justo donde no cabe el mensaje. */}
      {error &&
        (compact ? (
          <span className="verr" title={error}>
            no se ha guardado
          </span>
        ) : (
          <p className="verr">{error}</p>
        ))}
    </div>
  );
}

/**
 * El veredicto de máquina, siempre con su etiqueta y su coste.
 *
 * Nunca se pinta como si fuera de una persona, y el coste va pegado porque juzgar es
 * gasto real: si no se enseñara, sabríamos menos de nuestro propio gasto que del del
 * usuario (D-088).
 */
export function JudgeChip({ annotation }: { annotation: Annotation }) {
  const juez = annotation.judge;
  return (
    <span
      className={`chip judge ${annotation.verdict}`}
      title={
        juez
          ? `${juez.model} · prompt ${juez.prompt_version} · ${juez.input_tokens}+${juez.output_tokens} tokens`
          : "veredicto de máquina"
      }
    >
      juez: {annotation.verdict === "pass" ? "bien" : annotation.verdict === "fail" ? "mal" : "?"}
      {juez && (
        <em className="pro">
          {juez.cost_unknown ? " coste desconocido" : ` ${money(juez.cost_usd)}`}
        </em>
      )}
    </span>
  );
}

/** La marca compacta que lleva una fila del explorador. */
export function VerdictDots({ annotations }: { annotations: Annotation[] }) {
  const humana = annotations.find((a) => a.source === "human");
  const maquina = annotations.find((a) => a.source === "llm_judge");
  if (!humana && !maquina) return null;
  return (
    <span className="vdots">
      {humana && (
        <i className={`vdot ${humana.verdict}`} title={`persona: ${humana.verdict}`} />
      )}
      {maquina && (
        <i className={`vdot judge ${maquina.verdict}`} title={`juez: ${maquina.verdict}`} />
      )}
    </span>
  );
}
