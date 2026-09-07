"use client";

/** Cualquier fallo no previsto. En modo diagnóstico, sin tecnicismos. */
export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <main className="reading">
      <div className="state bad">
        <h2>Algo ha fallado al cargar esta pantalla</h2>
        <p>
          No hemos podido preparar los datos. Suele ser cosa de un momento: vuelve a
          intentarlo y, si sigue igual, revisa que el backend esté en pie.
        </p>
        <div className="actions">
          <button type="button" className="btn primary" onClick={reset}>
            Reintentar
          </button>
        </div>
      </div>
    </main>
  );
}
