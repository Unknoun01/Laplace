"use client";

import { t } from "@/lib/textos";

/** Cualquier fallo no previsto. En modo diagnóstico, sin tecnicismos. */
export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <main className="reading">
      <div className="state bad">
        <h2>{t("err.titulo")}</h2>
        <p>{t("err.texto")}</p>
        <div className="actions">
          <button type="button" className="btn primary" onClick={reset}>
            {t("estado.reintentar")}
          </button>
        </div>
      </div>
    </main>
  );
}
