"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getReplayEstado,
  lanzarReplay,
  planDeReplay,
  type ReplayPeticion,
  type ReplayPlan,
  type ReplayTirada,
} from "@/lib/api";
import { money } from "@/lib/format";
import { usePermisos } from "@/lib/permisos";
import { t } from "@/lib/textos";

/** El mismo tope que la orden que enseña la pantalla (`--tope 1`). */
const TOPE_USD = 1;

/**
 * Lanzar `laplace replay` con un botón (D-214). Sólo en la versión local: en la nube el
 * backend dice `disponible: false` y no se pinta nada. Gasta dinero de verdad con la
 * clave del entorno de `laplace ui`, así que antes de lanzar enseña lo peor que puede
 * costar y pide confirmar; la clave nunca se pide aquí.
 */
export function BotonReplay({
  project,
  conjunto,
  modelo,
  propuesta,
}: {
  project: string;
  conjunto: string;
  modelo: string;
  /** Probar el arreglo de texto de este hallazgo, con el mismo modelo (D-221). */
  propuesta?: string;
}) {
  const { escribir } = usePermisos(project);
  const [disponible, setDisponible] = useState(false);
  const [tirada, setTirada] = useState<ReplayTirada | null>(null);
  const [plan, setPlan] = useState<ReplayPlan | null>(null);
  const [error, setError] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const peticion: ReplayPeticion = {
    project_id: project,
    dataset: conjunto,
    model: modelo,
    max_usd: TOPE_USD,
    ...(propuesta ? { propuesta } : {}),
  };

  const mirar = useCallback(() => {
    getReplayEstado(project)
      .then((e) => {
        setDisponible(e.disponible);
        setTirada(e.tirada);
      })
      .catch(() => setDisponible(false));
  }, [project]);
  useEffect(mirar, [mirar]);
  useEffect(() => {
    if (tirada?.estado !== "corriendo") return;
    const id = setInterval(mirar, 1500);
    return () => clearInterval(id);
  }, [tirada?.estado, mirar]);

  if (!disponible || !escribir || !modelo) return null;

  const pedirPlan = async () => {
    setError("");
    setOcupado(true);
    try {
      setPlan(await planDeReplay(peticion));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setOcupado(false);
    }
  };
  const lanzar = async () => {
    if (!plan) return;
    setError("");
    setOcupado(true);
    try {
      setTirada(await lanzarReplay({ ...peticion, acepta_usd: plan.peor_usd }));
      setPlan(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setOcupado(false);
    }
  };

  const corriendo = tirada?.estado === "corriendo";
  const r = tirada?.resultado;
  return (
    <div className="pp-estado" data-replay>
      {!plan && !corriendo && (
        <button type="button" className="btn small" onClick={pedirPlan} disabled={ocupado}>
          {t("replay.boton", { modelo })}
        </button>
      )}
      {plan && (
        <div>
          <p>
            {t("replay.plan", {
              n: plan.plan.calls,
              modelo,
              original: money(plan.plan.original_usd),
              minimo: plan.plan.original_unknown ? t("replay.al_menos") : "",
              peor: money(plan.peor_usd),
            })}
          </p>
          {plan.plan.worst_usd > plan.plan.max_usd && <p>{t("replay.pasa_del_tope")}</p>}
          {!plan.clave && <p className="verr">{t("replay.sin_clave", { variable: plan.variable })}</p>}
          <button
            type="button"
            className="btn small"
            onClick={lanzar}
            disabled={ocupado || !plan.clave}
          >
            {t("replay.confirmar", { peor: money(plan.peor_usd) })}
          </button>{" "}
          <button type="button" className="btn small" onClick={() => setPlan(null)}>
            {t("replay.cancelar")}
          </button>
        </div>
      )}
      {corriendo && <p>{t("replay.corriendo", { hechas: tirada.hechas, total: tirada.total })}</p>}
      {tirada?.estado === "hecha" && r && (
        <p>
          {t("replay.hecha", {
            n: r.calls_done,
            gastado: money(r.spent_usd),
            run: r.replay_run_id,
          })}
          {r.calls_skipped_by_cap > 0 && " " + t("replay.omitidas", { n: r.calls_skipped_by_cap })}
          {r.cases_failed > 0 && " " + t("replay.fallidas", { n: r.cases_failed })}
        </p>
      )}
      {tirada?.estado === "cancelada" && <p>{t("replay.cancelada")}</p>}
      {tirada?.estado === "fallo" && <p className="verr">{tirada.error}</p>}
      {error && <p className="verr">{error}</p>}
    </div>
  );
}
