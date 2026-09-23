"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { type Me, getApiKey, getInstance, getMe, loadDemo, setApiKey } from "@/lib/api";

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
      <h2>No podemos conectar con el backend</h2>
      <p>
        No responde. Si estás en local, arráncalo con <code>laplace ui</code>; si es la
        instalación completa, con <code>docker compose up</code>.
      </p>
      {mensaje && <pre>{mensaje}</pre>}
      <div className="actions">
        <a href="." className="btn primary">
          Reintentar
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

export function NoProject() {
  return (
    <div className="state">
      <h2>Todavía no hay ningún proyecto</h2>
      <p>
        En cuanto tu agente envíe su primera ejecución, aparecerá aquí. Instrumentarlo es
        una línea:
      </p>
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
      En esta instalación el agente necesita una clave para enviar trazas.{" "}
      <Link href="/organizacion">Créala en Organización</Link>: ahí mismo sale el
      fragmento de arriba con la clave puesta.
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
      setError(e instanceof Error ? e.message : "no se han podido cargar");
      setCargando(false);
    }
  }

  return (
    <div style={{ marginTop: 18 }}>
      <p>
        ¿Sólo quieres verlo funcionando? Carga unas trazas de ejemplo —datos inventados, en
        un proyecto aparte llamado «demo»— y mira qué detecta.
      </p>
      {local ? (
        <div className="actions" style={{ paddingTop: 8 }}>
          <button type="button" className="btn primary" onClick={cargar} disabled={cargando}>
            {cargando ? "Cargando…" : "Cargar datos de ejemplo"}
          </button>
        </div>
      ) : (
        <p>
          Desde una terminal: <code>laplace demo</code>.
        </p>
      )}
      {error && <p className="verr">{error}</p>}
    </div>
  );
}

export function NoTracesYet({ project }: { project: string }) {
  return (
    <div className="state">
      <h2>Esperando la primera ejecución de «{project}»</h2>
      <p>
        El proyecto existe pero no ha llegado ninguna traza en el rango que estás mirando.
        Prueba a ampliar el rango en la barra de arriba, o lanza tu agente con Laplace
        activado.
      </p>
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
        {aviso
          ? "No hemos encontrado nada que arreglar, pero no hemos podido mirarlo todo"
          : "No estás tirando dinero ahora mismo"}
      </h2>
      <p>
        Hemos buscado llamadas repetidas, pasos que usan un modelo más caro del que
        necesitan y contexto que se reenvía sin hacer falta. No hay nada de eso en este
        rango.
      </p>
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
export function NeedsKey({ mensaje }: { mensaje?: string }) {
  const [valor, setValor] = useState(getApiKey());
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
      <h2>Esta instalación pide una clave</h2>
      <p>
        {mensaje && mensaje !== "credencial inválida"
          ? mensaje
          : "La clave va en cada petición y se guarda sólo en este navegador. Si no tienes una, la crea quien administra esta organización, en Organización → Claves."}
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
            setApiKey(valor);
            window.location.reload();
          }}
        >
          Guardar y entrar
        </button>
        <a href="/entrar" className="btn">
          Entrar con tu cuenta
        </a>
      </div>
      <p className="disclaimer">
        Se guarda en el almacenamiento de este navegador, no se manda a ningún sitio más
        que a este Laplace, y va en la cabecera <code>Authorization</code> y nunca en la
        URL —lo que va en la URL acaba en los logs de cualquier proxy por el que pase—.
      </p>
    </div>
  );
}

/** Hay clave, pero no para este proyecto. */
export function NotYours({ mensaje }: { mensaje?: string }) {
  return (
    <div className="state">
      <h2>Tu clave no da acceso a este proyecto</h2>
      <p>{mensaje || "Esta clave sirve para otro proyecto de esta instalación."}</p>
      <div className="actions">
        <button
          type="button"
          className="btn"
          onClick={() => {
            setApiKey("");
            window.location.reload();
          }}
        >
          Usar otra clave
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
          Volver
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
