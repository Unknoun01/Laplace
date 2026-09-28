"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { DEFAULT_DAYS, type Me, RANGES, getMe, listProjects, logout } from "@/lib/api";
import { SelectorIdioma } from "@/lib/i18n";
import { t } from "@/lib/textos";
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
  const [me, setMe] = useState<Me | null>(null);
  // En las pantallas de entrar no hay nada que navegar todavía: sin sesión, cada pestaña
  // sería un 401 que devuelve aquí (D-127).
  const acceso = ["/entrar", "/configurar", "/invitacion"].some((r) => pathname.startsWith(r));
  useEffect(() => {
    getMe()
      .then(setMe)
      .catch(() => setMe(null));
    if (acceso) return;
    listProjects()
      .then(setProjects)
      .catch(() => setProjects([]));
  }, [acceso]);

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

  // Hacia qué lado quedan pestañas escondidas, para difuminar sólo ese borde (B1).
  const nav = useRef<HTMLElement>(null);
  useEffect(() => {
    const barra = nav.current;
    if (!barra) return;
    const medir = () => {
      const sobra = barra.scrollWidth - barra.clientWidth;
      const x = barra.scrollLeft;
      barra.dataset.desborda =
        sobra <= 1 ? "" : x <= 1 ? "derecha" : x >= sobra - 1 ? "izquierda" : "ambos";
    };
    medir();
    barra.addEventListener("scroll", medir, { passive: true });
    const observador = new ResizeObserver(medir);
    observador.observe(barra);
    return () => {
      barra.removeEventListener("scroll", medir);
      observador.disconnect();
    };
  }, [acceso]);

  const keep = new URLSearchParams();
  if (project) keep.set("project", project);
  if (days !== DEFAULT_DAYS) keep.set("days", String(days));
  const query = keep.toString() ? `?${keep.toString()}` : "";

  if (acceso) {
    return (
      <header className="topbar">
        <span className="mark">
          <Logo />
          Laplace
        </span>
        <div className="pick">
          <SelectorIdioma />
        </div>
      </header>
    );
  }

  return (
    <header className="topbar">
      <Link href={`/${query}`} className="mark">
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
          <path
            d="M2 15.5 C 5 15.5, 6 2.5, 9 2.5 S 13 15.5, 16 15.5"
            stroke="var(--iris)"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
          <circle cx="9" cy="2.5" r="1.8" fill="var(--iris)" />
        </svg>
        Laplace
      </Link>
      {/* El proyecto va junto al logo, como una ruta: es el contexto de todo lo demás, y
          al final de la barra se cortaba en móvil («demo…») (D-125). */}
      <div className="brand-ctx">
        <span className="slash" aria-hidden>
          /
        </span>
        <select
          className="ctx proyecto"
          aria-label={t("barra.proyecto")}
          value={project}
          onChange={(event) => setParam("project", event.target.value)}
        >
          {projects.length === 0 && <option value="">{t("barra.sin_proyectos")}</option>}
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.id}
            </option>
          ))}
        </select>
      </div>

      <nav className="nav" ref={nav}>
        <Link href={`/${query}`} aria-current={
            pathname === "/" || pathname.startsWith("/problema") ? "page" : undefined
          }>
          {t("nav.diagnostico")}
        </Link>
        <Link
          href={`/trazas${query}`}
          aria-current={
            pathname.startsWith("/traza")
              ? "page"
              : undefined
          }
        >
          {t("nav.trazas")}
        </Link>
        <Link href={`/panel${query}`} aria-current={pathname === "/panel" ? "page" : undefined}>
          {t("nav.panel")}
        </Link>
        <Link
          href={`/evaluaciones${query}`}
          aria-current={pathname === "/evaluaciones" ? "page" : undefined}
        >
          {t("nav.evaluaciones")}
        </Link>
        <Link
          href={`/prompts${query}`}
          aria-current={pathname === "/prompts" ? "page" : undefined}
        >
          {t("nav.prompts")}
        </Link>
        <Link
          href={`/ajustes${query}`}
          aria-current={pathname === "/ajustes" ? "page" : undefined}
        >
          {t("nav.ajustes")}
        </Link>
      </nav>

      <div className="pick">
        <select
          className="ctx"
          aria-label={t("barra.rango")}
          value={days}
          onChange={(event) => setParam("days", event.target.value)}
        >
          {RANGES.map((r) => (
            <option key={r.days} value={r.days}>
              {t(r.label)}
            </option>
          ))}
        </select>
        <SelectorIdioma />
        <ModeToggle />
        {me?.user && <MenuUsuario me={me} />}
      </div>
    </header>
  );
}

function Logo() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <path
        d="M2 15.5 C 5 15.5, 6 2.5, 9 2.5 S 13 15.5, 16 15.5"
        stroke="var(--iris)"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <circle cx="9" cy="2.5" r="1.8" fill="var(--iris)" />
    </svg>
  );
}

/**
 * Quién eres y la salida (D-127). Sólo en la nube: en local no hay nadie más que tú.
 * La inicial y no el email entero: la barra no tiene sitio, y el email sale al pasar.
 */
function MenuUsuario({ me }: { me: Me }) {
  const user = me.user!;
  const inicial = (user.name || user.email).trim().charAt(0).toUpperCase();
  return (
    <details className="usuario">
      <summary title={user.email} aria-label={t("barra.tu_cuenta", { email: user.email })}>
        {inicial}
      </summary>
      <div className="usuario-menu">
        <p>
          {user.name && <strong>{user.name}</strong>}
          <span>{user.email}</span>
        </p>
        <Link href="/organizacion">{t("barra.organizacion")}</Link>
        <button
          type="button"
          onClick={async () => {
            try {
              await logout();
            } finally {
              window.location.href = "/entrar";
            }
          }}
        >
          {t("barra.salir")}
        </button>
      </div>
    </details>
  );
}

const STORAGE_KEY = "laplace.mode";

/**
 * Sencillo / Avanzado. Es la columna vertebral del producto: el mismo contenido
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

  // Un interruptor y no dos botones: «Sencillo» al lado de la pestaña «Diagnóstico» se
  // leía como otra navegación, y dos botones comían el sitio que la barra no tiene.
  return (
    <button
      type="button"
      className="switch"
      role="switch"
      aria-checked={pro}
      onClick={() => change(!pro)}
      title={t("barra.avanzado_ayuda")}
    >
      <i aria-hidden />
      {t("barra.avanzado")}
    </button>
  );
}
