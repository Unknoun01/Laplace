import { NextResponse } from "next/server";
import { getTrace } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * Descarga de una traza en JSON.
 *
 * El navegador no puede llamar al backend directamente (en Docker vive en una red
 * interna), así que la web hace de puente. Devuelve exactamente lo que sirve la API:
 * el árbol con coste por nodo y los payloads en crudo.
 */
export async function GET(_request: Request, { params }: { params: { traceId: string } }) {
  const trace = await getTrace(params.traceId);
  if (!trace) {
    return NextResponse.json({ error: "traza no encontrada" }, { status: 404 });
  }

  return new NextResponse(JSON.stringify(trace, null, 2), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "content-disposition": `attachment; filename="${params.traceId}.json"`,
    },
  });
}
