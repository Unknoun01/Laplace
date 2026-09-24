"use client";

import Link from "next/link";
import { money, number, timestamp } from "@/lib/format";
import type { ObservedStep, PromptsView } from "@/lib/types";

export function SinAdoptar({ project }: { project: string }) {
  return (
    <section className="sec">
      <h2>Todavía no gestionas ningún prompt aquí</h2>
      <p className="lead">
        Tus prompts viven en tu código, que es un sitio perfectamente razonable. Lo que se
        pierde con eso es poder responder a «¿cuánto me costaba el de antes y cuánto
        acertaba?», porque un <code>git blame</code> sabe qué cambió y no sabe qué pasó
        después.
      </p>
      <p className="lead">
        Si los mueves aquí, el SDK los sirve y deja escrito en cada traza con qué versión
        se ejecutó. <strong>Laplace sigue sin ejecutar nada tuyo</strong>: sólo te da el
        texto, y si Laplace no responde, el SDK usa la copia guardada o el texto de reserva
        que le pases.
      </p>
      <pre>{`import laplace
laplace.init(project="${project}")

sistema = laplace.get_prompt("resumen", fallback=SISTEMA_DEL_CODIGO)
respuesta = cliente.messages.create(
    model="claude-haiku-4-5",
    system=sistema.render(idioma="es"),
    messages=[{"role": "user", "content": pregunta}],
)`}</pre>
      <p className="disclaimer">
        Mientras tanto, aquí abajo está lo que sí se puede saber de tus trazas: qué pasos
        han cambiado de instrucciones y cuándo.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------------
// Lo inferido de las trazas
// ---------------------------------------------------------------------------------

export function Observados({
  vista,
  query,
  managed,
}: {
  vista: PromptsView;
  query: string;
  managed: boolean;
}) {
  if (vista.observed.length === 0) {
    return (
      <section className="sec">
        <h2>Lo que se ve en tus trazas</h2>
        <p className="lead">{vista.observed_unavailable || "Nada todavía."}</p>
      </section>
    );
  }

  return (
    <section className="sec">
      <h2>Lo que se ve en tus trazas</h2>
      <p className="lead">
        {managed
          ? "Esto sale de las trazas, no de la gestión de prompts: son los juegos de instrucciones con los que se ha visto ejecutar cada paso. Sirve para ver si algo cambió por fuera de aquí."
          : "Cada paso de tu agente, y los juegos de instrucciones con los que se le ha visto ejecutar en este rango. Dos instrucciones distintas bajo el mismo paso son un cambio de prompt: no sabemos el texto entero —sólo guardamos el principio—, pero sí cuándo cambió y qué costó."}
      </p>
      {vista.observed.map((paso) => (
        <PasoObservado key={paso.label} paso={paso} />
      ))}
      <p className="disclaimer">
        ¿Ves un cambio que no reconoces?{" "}
        <Link href={`/trazas${query}`}>Ábrelo en el explorador</Link> y mira las
        instrucciones completas de esas ejecuciones.
      </p>
    </section>
  );
}

export function PasoObservado({ paso }: { paso: ObservedStep }) {
  return (
    <article className="card static">
      <div className="card-top">
        <h3>{paso.label}</h3>
        <div className="price">
          {paso.unstable ? "—" : paso.variants.length}
          <small>
            {paso.unstable
              ? "no son versiones"
              : paso.variants.length === 1
                ? "juego de instrucciones"
                : "juegos de instrucciones"}
          </small>
        </div>
      </div>

      {paso.unstable ? (
        <p className="unattributed">{paso.note}</p>
      ) : (
        <div className="ancha">
        <div className="tbl-scroll">
          <table className="tbl">
            <thead>
              <tr>
                <th>Instrucciones (principio)</th>
                <th className="r">Por ejecución</th>
                <th className="r">Ejecuciones</th>
                <th className="r hide-sm">Desde</th>
                <th className="r hide-sm">Hasta</th>
              </tr>
            </thead>
            <tbody>
              {paso.variants.map((v) => (
                <tr key={v.step_key}>
                  <td>
                    <span className="hint">{v.hint || "(sin instrucciones capturadas)"}</span>
                    <div className="meta pro">{v.step_key}</div>
                  </td>
                  <td className="r money">
                    {v.cost_per_execution_usd === null ? "—" : money(v.cost_per_execution_usd)}
                  </td>
                  <td className="r">{number(v.traces)}</td>
                  <td className="r hide-sm">{v.first_seen ? timestamp(v.first_seen) : "—"}</td>
                  <td className="r hide-sm">{v.last_seen ? timestamp(v.last_seen) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </div>
      )}
    </article>
  );
}

// ---------------------------------------------------------------------------------
// Crear
// ---------------------------------------------------------------------------------
