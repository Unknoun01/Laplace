"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
  type Me,
  RANGES,
  entrarConClave,
  getInstance,
  getMe,
  loadDemo,
  olvidarClave,
} from "@/lib/api";
import { dayHour, windowLabel } from "@/lib/format";
import { tr } from "@/lib/i18n";
import { t } from "@/lib/textos";

/**
 * El endpoint al que apuntar el SDK: el origen desde el que se está sirviendo esta
 * pantalla. En modo local es `http://127.0.0.1:8100`, y decirlo aquí ahorra el viaje a
 * la documentación que el modo local promete no hacer falta.
 */
function endpointActual(): string {
  if (typeof window === "undefined") return "";
  return window.location.origin;
}

/**
 * Estados. Son lo que separa una demo de un producto acabado: un proyecto vacío, un
 * backend caído o un diagnóstico limpio tienen que verse a propósito, no como una
 * pantalla rota.
 */

export function BackendDown({ mensaje }: { mensaje?: string }) {
  return (
    <div className="state bad">
      <h2>{t("estado.sin_backend.titulo")}</h2>
      <p>
        {tr("estado.sin_backend.texto", {
          local: <code>laplace ui</code>,
          docker: <code>docker compose up</code>,
        })}
      </p>
      {mensaje && <pre>{mensaje}</pre>}
      <div className="actions">
        <a href="." className="btn primary">
          {t("estado.reintentar")}
        </a>
      </div>
    </div>
  );
}

/** Mientras llegan los datos. Sobrio: es lo que se ve durante 200 ms, no una pantalla. */
export function Cargando() {
  return (
    <main className="reading">
      <HeroSkeleton />
      <CardsSkeleton />
    </main>
  );
}

/** El esqueleto del inicio, con su carril: del mismo tamaño para que nada salte. */
export function CargandoDiagnostico() {
  return (
    <main className="diag">
      <section className="hero">
        <span className="sk" style={{ width: 280, height: 29 }} />
        <span className="sk" style={{ width: 160, height: 14, margin: "8px 0 24px" }} />
        <div className="pair">
          <span className="sk" style={{ width: 190, height: 53 }} />
          <span className="sk" style={{ width: 190, height: 53 }} />
        </div>
        <span className="sk" style={{ height: 8, marginTop: 24 }} />
      </section>
      <aside className="diag-rail">
        <span className="sk" style={{ height: 96 }} />
        <span className="sk" style={{ height: 72 }} />
      </aside>
      <div className="diag-lista">
        <CardsSkeleton />
      </div>
    </main>
  );
}

export function NoProject() {
  return (
    <div className="state">
      <h2>{t("estado.sin_proyecto.titulo")}</h2>
      <p>{t("estado.sin_proyecto.texto")}</p>
      <pre>
        {`import laplace\n\nlaplace.init(\n    project=\"mi-agente\",\n    endpoint=\"${endpointActual()}\",\n)`}
      </pre>
      <PrimerProyecto />
      <CargarDemo />
    </div>
  );
}

/**
 * Con cuenta, el primer paso no es copiar un fragmento: es crear la clave del primer
 * proyecto, que es lo que lo pone a nombre de tu organización (D-127).
 */
function PrimerProyecto() {
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    getMe()
      .then(setMe)
      .catch(() => setMe(null));
  }, []);
  if (!me?.user) return null;
  return (
    <p style={{ marginTop: 14 }}>
      {tr("estado.primer_proyecto", {
        enlace: <Link href="/organizacion">{t("estado.primer_proyecto.enlace")}</Link>,
      })}
    </p>
  );
}

/**
 * Los datos de ejemplo, con un botón (D-123).
 *
 * Antes era «teclea `laplace demo` en otra terminal», que es justo el paso en el que
 * se pierde quien acaba de instalar. En la nube no se ofrece: meter datos inventados en
 * una instalación compartida no es lo que nadie espera de un botón.
 */
function CargarDemo() {
  const [local, setLocal] = useState(false);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getInstance()
      .then((i) => setLocal(i.local && i.operator))
      .catch(() => setLocal(false));
  }, []);

  async function cargar() {
    setCargando(true);
    setError("");
    try {
      await loadDemo();
      window.location.href = "/?project=demo";
    } catch (e) {
      setError(e instanceof Error ? e.message : t("estado.demo.error"));
      setCargando(false);
    }
  }

  return (
    <div style={{ marginTop: 18 }}>
      <p>{t("estado.demo.texto")}</p>
      {local ? (
        <div className="actions" style={{ paddingTop: 8 }}>
          <button type="button" className="btn primary" onClick={cargar} disabled={cargando}>
            {cargando ? t("estado.demo.cargando") : t("estado.demo.boton")}
          </button>
        </div>
      ) : (
        <p>{tr("estado.demo.terminal", { comando: <code>laplace demo</code> })}</p>
      )}
      {error && <p className="verr">{error}</p>}
    </div>
  );
}

/**
 * Sin ejecuciones que enseñar. Son dos situaciones que antes se decían igual (A1 de la
 * auditoría del rediseño): un proyecto al que **nunca** ha llegado nada, que necesita
 * instrucciones, y uno con trazas **fuera del rango** que se mira, que necesita otro
 * rango. Decir «esperando la primera ejecución» en el segundo caso hace pensar que la
 * instalación no funciona. `lastSeen` sale de `/api/projects`.
 */
export function NoTracesYet({
  project,
  lastSeen,
  days,
}: {
  project: string;
  lastSeen?: string | null;
  days?: number;
}) {
  const ruta = usePathname();
  const params = useSearchParams();
  if (lastSeen && days) {
    const haceDias = (Date.now() - Date.parse(lastSeen)) / 86_400_000;
    // El rango más corto de los que hay que ya la incluye.
    const rango = RANGES.find((r) => r.days > days && r.days >= haceDias);
    const mayor = RANGES[RANGES.length - 1];
    const otros = new URLSearchParams(params.toString());
    if (rango) otros.set("days", String(rango.days));
    return (
      <div className="state">
        <h2>{t("estado.rango_vacio.titulo", { ventana: windowLabel(days) })}</h2>
        <p>
          {rango || haceDias <= days
            ? t("estado.rango_vacio.ultima", { fecha: dayHour(lastSeen) })
            : t("estado.rango_vacio.fuera", { fecha: dayHour(lastSeen), rango: t(mayor.label) })}
        </p>
        {rango && (
          <div className="actions">
            <Link className="btn primary" href={`${ruta}?${otros.toString()}`}>
              {t("estado.rango_vacio.boton", { ventana: windowLabel(rango.days) })}
            </Link>
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="state">
      <h2>{t("estado.sin_trazas.titulo", { proyecto: project })}</h2>
      <p>{t("estado.sin_trazas.texto")}</p>
      <pre>
        {`import laplace\n\nlaplace.init(\n    project=\"${project}\",\n    endpoint=\"${endpointActual()}\",\n)`}
      </pre>
    </div>
  );
}

/**
 * «No hay nada que arreglar», que es la frase más peligrosa del producto.
 *
 * Sólo es una buena noticia si de verdad hemos podido mirar. Cuando la cobertura es
 * baja, el mismo silencio significa «no te entendemos», así que el bloque deja de ser
 * verde y lo dice: `aviso` llega desde el inicio con la lectura de la cobertura (D-096).
 */
export function NothingToFix({
  children,
  aviso,
}: {
  children?: React.ReactNode;
  aviso?: React.ReactNode;
}) {
  return (
    <div className={`state ${aviso ? "warn" : "good"}`}>
      <h2>
        {aviso ? t("estado.nada.titulo_con_aviso") : t("estado.nada.titulo")}
      </h2>
      <p>{t("estado.nada.texto")}</p>
      {aviso && <p className="whynot">{aviso}</p>}
      {children}
    </div>
  );
}

/**
 * Esta instalación pide clave y no la tenemos (o la que hay no vale).
 *
 * No es un login: no hay cuentas todavía. Es la clave de API que crea el operador con
 * `python -m laplace_backend.keys create`, guardada en este navegador. Se dice de dónde
 * sale, porque un formulario que pide un secreto sin explicar cuál es lo que hace que
 * la gente pegue el primero que encuentra.
 */
export function NeedsKey({ mensaje, codigo }: { mensaje?: string; codigo?: string }) {
  const [valor, setValor] = useState("");
  const [fallo, setFallo] = useState("");
  const [me, setMe] = useState<Me | null>(null);

  // Con cuentas (D-127), quien llega sin sesión no tiene por qué saber qué es una clave
  // de API: se le manda a entrar, o a configurar si la instalación está por estrenar.
  // Pegar una clave queda como alternativa, para quien de verdad tiene una.
  useEffect(() => {
    getMe()
      .then((m) => {
        setMe(m);
        if (m.mode !== "nube" || m.user || m.by_key) return;
        const vuelta = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = m.needs_setup ? "/configurar" : `/entrar?next=${vuelta}`;
      })
      .catch(() => setMe(null));
  }, []);

  if (me && me.mode === "nube" && !me.user && !me.by_key) return <Cargando />;

  return (
    <div className="state">
      <h2>{t("estado.clave.titulo")}</h2>
      <p>
        {/* «Credencial inválida» no ayuda a nadie a conseguir una: se explica de dónde sale. */}
        {mensaje && codigo !== "credencial_invalida"
          ? mensaje
          : t("estado.clave.sin_clave")}
      </p>
      <div className="actions">
        <input
          className="field"
          type="password"
          value={valor}
          placeholder="lp_…"
          onChange={(e) => setValor(e.target.value)}
          style={{ minWidth: 280 }}
        />
        <button
          type="button"
          className="btn primary"
          onClick={() => {
            setFallo("");
            entrarConClave(valor)
              .then(() => window.location.reload())
              .catch((e: Error) => setFallo(e.message));
          }}
        >
          {t("estado.clave.guardar")}
        </button>
        <a href="/entrar" className="btn">
          {t("estado.clave.con_cuenta")}
        </a>
      </div>
      {fallo && <p className="disclaimer">{fallo}</p>}
      <p className="disclaimer">{t("estado.clave.cookie")}</p>
    </div>
  );
}

/** Hay clave, pero no para este proyecto. */
export function NotYours({ mensaje }: { mensaje?: string }) {
  return (
    <div className="state">
      <h2>{t("estado.ajeno.titulo")}</h2>
      <p>{mensaje || t("estado.ajeno.texto")}</p>
      <div className="actions">
        <button
          type="button"
          className="btn"
          onClick={() => {
            olvidarClave().finally(() => window.location.reload());
          }}
        >
          {t("estado.ajeno.otra")}
        </button>
      </div>
    </div>
  );
}

export function NotFound({ title, body, back }: { title: string; body: string; back: string }) {
  return (
    <div className="state">
      <h2>{title}</h2>
      <p>{body}</p>
      <div className="actions">
        <Link href={back} className="btn">
          {t("estado.volver")}
        </Link>
      </div>
    </div>
  );
}

/** Esqueletos de carga: sobrios y del tamaño real, para que nada salte al llegar. */
export function HeroSkeleton() {
  return (
    <section className="hero">
      <span className="sk" style={{ width: 260, height: 15, marginBottom: 22 }} />
      <div className="pair">
        <span className="sk" style={{ width: 190, height: 52 }} />
        <span className="sk" style={{ width: 190, height: 52 }} />
      </div>
      <span className="sk" style={{ height: 10, marginTop: 24 }} />
    </section>
  );
}

export function CardsSkeleton({ count = 3 }: { count?: number }) {
  return (
    <section className="sec">
      <span className="sk" style={{ width: 200, height: 17, marginBottom: 18 }} />
      {Array.from({ length: count }).map((_, index) => (
        <span key={index} className="sk" style={{ height: 118, marginBottom: 12 }} />
      ))}
    </section>
  );
}

export function TableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div style={{ paddingTop: 18 }}>
      <span className="sk" style={{ height: 36, marginBottom: 2 }} />
      {Array.from({ length: rows }).map((_, index) => (
        <span key={index} className="sk" style={{ height: 44, marginBottom: 2, opacity: 0.7 }} />
      ))}
    </div>
  );
}
