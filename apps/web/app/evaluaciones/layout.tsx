import type { Metadata } from "next";

/** El título de la pestaña del navegador para esta sección (D-125). */
export const metadata: Metadata = { title: "Evaluaciones" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
