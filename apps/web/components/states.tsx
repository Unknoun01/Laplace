import Link from "next/link";

/**
 * Estados. Son lo que separa una demo de un producto acabado: un proyecto vacío, un
 * backend caído o un diagnóstico limpio tienen que verse a propósito, no como una
 * pantalla rota.
 */

export function BackendDown({ apiUrl }: { apiUrl: string }) {
  return (
    <div className="state bad">
      <h2>No podemos conectar con el backend</h2>
      <p>
        No responde en <code>{apiUrl}</code>. Levanta la infraestructura con{" "}
        <code>docker compose up</code> y vuelve a intentarlo.
      </p>
      <div className="actions">
        <Link href="/" className="btn primary">
          Reintentar
        </Link>
      </div>
    </div>
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
      <pre>{'import laplace\n\nlaplace.init(project="mi-agente")'}</pre>
      <p style={{ marginTop: 18 }}>
        ¿Sólo quieres verlo funcionando? Lanza el agente de ejemplo del repositorio:{" "}
        <code>python examples/agente_ejemplo.py</code>
      </p>
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
      <pre>{'import laplace\n\nlaplace.init(project="' + project + '")'}</pre>
    </div>
  );
}

export function NothingToFix({ children }: { children?: React.ReactNode }) {
  return (
    <div className="state good">
      <h2>No estás tirando dinero ahora mismo</h2>
      <p>
        Hemos buscado llamadas repetidas, pasos que usan un modelo más caro del que
        necesitan y contexto que se reenvía sin hacer falta. No hay nada de eso en este
        rango.
      </p>
      {children}
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
