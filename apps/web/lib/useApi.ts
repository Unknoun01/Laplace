"use client";

import { useEffect, useState } from "react";
import { ApiError } from "./api";

/**
 * Carga de datos para las pantallas.
 *
 * Las páginas piden sus datos desde el navegador, no desde el servidor de Next. El
 * motivo es el modo local: `laplace ui` sirve la interfaz desde el mismo proceso de
 * Python que la API, y una interfaz que necesitara Node para renderizarse en servidor
 * no cabría en un `pip install` (D-069). En la nube el resultado es el mismo, porque
 * mirar trazas es mirar lo que acaba de pasar y nada de esto se cachea.
 */
export type Estado<T> =
  | { fase: "cargando" }
  | { fase: "listo"; datos: T }
  | { fase: "sin-backend" }
  | { fase: "error"; error: Error };

export function useApi<T>(cargar: () => Promise<T>, deps: unknown[]): Estado<T> {
  const [estado, setEstado] = useState<Estado<T>>({ fase: "cargando" });

  useEffect(() => {
    let vigente = true;
    setEstado({ fase: "cargando" });
    cargar()
      .then((datos) => {
        if (vigente) setEstado({ fase: "listo", datos });
      })
      .catch((error: Error) => {
        if (!vigente) return;
        // Distinguir «no hay backend» de «el backend ha dicho que no» es la diferencia
        // entre una pantalla que ayuda y una que sólo dice que algo ha fallado.
        const caido = !(error instanceof ApiError);
        setEstado(caido ? { fase: "sin-backend" } : { fase: "error", error });
      });
    return () => {
      vigente = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return estado;
}
