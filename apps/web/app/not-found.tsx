import Link from "next/link";

export default function NotFound() {
  return (
    <main className="reading">
      <div className="state">
        <h2>Esta página no existe</h2>
        <p>El enlace puede estar mal, o apuntar a algo que ya no está.</p>
        <div className="actions">
          <Link href="/" className="btn primary">
            Ir al diagnóstico
          </Link>
        </div>
      </div>
    </main>
  );
}
