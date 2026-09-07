import { TableSkeleton } from "@/components/states";

export default function Loading() {
  return (
    <main>
      <div style={{ paddingTop: 32 }}>
        <span className="sk" style={{ width: 120, height: 17 }} />
      </div>
      <TableSkeleton />
    </main>
  );
}
