"use client";


export function Aviso({ ok, texto }: { ok: boolean; texto: string }) {
  if (!texto) return null;
  return <p className={ok ? "vok" : "verr"}>{texto}</p>;
}

// ---------------------------------------------------------------------------------
