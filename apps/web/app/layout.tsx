import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Laplace",
  description: "Observabilidad y optimización de agentes de IA.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body className="min-h-full">
        <div className="flex min-h-screen flex-col">
          <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/80 backdrop-blur dark:border-slate-800 dark:bg-slate-950/80">
            <div className="mx-auto flex h-14 max-w-[1800px] items-center gap-6 px-6">
              <Link href="/" className="flex items-center gap-2.5 font-semibold tracking-tight">
                <LaplaceMark />
                Laplace
              </Link>
              <span className="hidden text-sm text-slate-500 dark:text-slate-400 sm:block">
                Trazas
              </span>
            </div>
          </header>
          <main className="mx-auto w-full max-w-[1800px] flex-1 px-6 py-6">{children}</main>
        </div>
      </body>
    </html>
  );
}

function LaplaceMark() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden className="text-sky-500">
      <path
        d="M3 16.5C6.5 16.5 6 3.5 10 3.5s3.5 13 7 13"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}
