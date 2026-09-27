"use client";

import { useState } from "react";
import { t } from "@/lib/textos";

export function Aviso({ ok, texto }: { ok: boolean; texto: string }) {
  if (!texto) return null;
  return <p className={ok ? "vok" : "verr"}>{texto}</p>;
}

export function useAviso() {
  const [msg, setMsg] = useState({ ok: true, texto: "" });
  const intentar = async (fn: () => Promise<unknown>, texto: string) => {
    try {
      await fn();
      setMsg({ ok: true, texto });
      return true;
    } catch (e) {
      setMsg({ ok: false, texto: e instanceof Error ? e.message : t("org.error.generico") });
      return false;
    }
  };
  return { msg, intentar };
}

// ---------------------------------------------------------------------------------
