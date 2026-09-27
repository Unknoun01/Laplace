"use client";

import Link from "next/link";
import { t } from "@/lib/textos";

export default function NotFound() {
  return (
    <main className="reading">
      <div className="state">
        <h2>{t("nf.titulo")}</h2>
        <p>{t("nf.texto")}</p>
        <div className="actions">
          <Link href="/" className="btn primary">
            {t("nf.ir")}
          </Link>
        </div>
      </div>
    </main>
  );
}
