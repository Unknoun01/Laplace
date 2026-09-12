"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { DEFAULT_DAYS, RANGES, listProjects } from "@/lib/api";
import type { ProjectStats } from "@/lib/types";

/**
 * Barra superior: navegación, contexto (proyecto y rango) y el modo dual.
 *
 * El proyecto y el rango viven en la URL, no en un estado interno: así una pantalla
 * concreta se puede enlazar y compartir tal cual se está viendo.
 */
export function TopBar() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  // La lista de proyectos la pide la propia barra. Si el backend no responde se queda
  // vacía y cada pantalla explica el problema: el armazón nunca debe caerse por eso.
  const [projects, setProjects] = useState<ProjectStats[]>([]);
  useEffect(() => {
    listProjects()
      .then(setProjects)
      .catch(() => setProjects([]));
  }, []);

  const project = params.get("project") ?? projects[0]?.id ?? "";
  const days = Number(params.get("days")) || DEFAULT_DAYS;

  const setParam = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params.toString());
      next.set(key, value);
      // Cambiar de contexto invalida la paginación de la lista de trazas.
      next.delete("cursor");
      router.push(`${pathname}?${next.toString()}`);
    },
    [params, pathname, router],
  );

  const keep = new URLSearchParams();
  if (project) keep.set("project", project);
  if (days !== DEFAULT_DAYS) keep.set("days", String(days));
  const query = keep.toString() ? `?${keep.toString()}` : "";

  return (
    <header className="topbar">
      <Link href={`/${query}`} className="mark">
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
          <path
            d="M2 15.5 C 5 15.5, 6 2.5, 9 2.5 S 13 15.5, 16 15.5"
            stroke="#7C93F5"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
          <circle cx="9" cy="2.5" r="1.8" fill="#7C93F5" />
        </svg>
        Laplace
      </Link>

      <nav className="nav">
        <Link href={`/${query}`} aria-current={pathname === "/" ? "page" : undefined}>
          Diagnóstico
        </Link>
        <Link
          href={`/trazas${query}`}
          aria-current={
            pathname.startsWith("/traza") || pathname.startsWith("/problema")
              ? "page"
              : undefined
          }
        >
          Trazas
        </Link>
        <Link href={`/panel${query}`} aria-current={pathname === "/panel" ? "page" : undefined}>
          Panel
        </Link>
        <Link
          href={`/evaluaciones${query}`}
          aria-current={pathname === "/evaluaciones" ? "page" : undefined}
        >
          Evaluaciones
        </Link>
        <Link
          href={`/prompts${query}`}
          aria-current={pathname === "/prompts" ? "page" : undefined}
        >
          Prompts
        </Link>
      </nav>

      <div className="pick">
        <select
          className="ctx"
          aria-label="Proyecto"
          value={project}
          onChange={(event) => setParam("project", event.target.value)}
        >
          {projects.length === 0 && <option value="">Sin proyectos</option>}
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.id}
            </option>
          ))}
        </select>
        <select
          className="ctx"
          aria-label="Rango temporal"
          value={days}
          onChange={(event) => setParam("days", event.target.value)}
        >
          {RANGES.map((r) => (
            <option key={r.days} value={r.days}>
              {r.label}
            </option>
          ))}
        </select>
        <ModeToggle />
      </div>
    </header>
  );
}

const STORAGE_KEY = "laplace.mode";

/**
 * Diagnóstico / Avanzado. Es la columna vertebral del producto: el mismo contenido
 * sirve a alguien inexperto y a un desarrollador, sin partir el producto en dos.
 *
 * El modo es global y se recuerda entre visitas: un dev no debería tener que darle a
 * "Avanzado" cada vez que entra.
 */
function ModeToggle() {
  const [pro, setPro] = useState(false);

  useEffect(() => {
    setPro(document.body.dataset.mode === "pro");
  }, []);

  const change = (value: boolean) => {
    setPro(value);
    document.body.dataset.mode = value ? "pro" : "simple";
    try {
      window.localStorage.setItem(STORAGE_KEY, value ? "pro" : "simple");
    } catch {
      // Navegador con almacenamiento bloqueado: el modo dura lo que la sesión.
    }
  };

  return (
    <div className="seg" role="group" aria-label="Nivel de detalle">
      <button type="button" aria-pressed={!pro} onClick={() => change(false)}>
        Diagnóstico
      </button>
      <button type="button" aria-pressed={pro} onClick={() => change(true)}>
        Avanzado
      </button>
    </div>
  );
}
