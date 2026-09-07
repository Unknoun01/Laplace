import { CardsSkeleton, HeroSkeleton } from "@/components/states";

/** Esqueleto del inicio mientras llega el diagnóstico. */
export default function Loading() {
  return (
    <main className="reading">
      <HeroSkeleton />
      <CardsSkeleton />
    </main>
  );
}
