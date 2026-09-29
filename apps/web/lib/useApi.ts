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
type Estado<T> =
  | { fase: "cargando" }
  | { fase: "listo"; datos: T }
  | { fase: "sin-backend" }
  /** La instalación pide clave y no la hay, o no vale. */
  | { fase: "sin-clave"; error: ApiError }
  /** Hay clave, pero no para lo que se está pidiendo. */
  | { fase: "sin-permiso"; error: ApiError }
  | { fase: "error"; error: Error };

export function useApi<T>(
  cargar: (senal: AbortSignal) => Promise<T>,
  deps: unknown[],
): Estado<T> {
  const [estado, setEstado] = useState<Estado<T>>({ fase: "cargando" });

  useEffect(() => {
    let vigente = true;
    // Al cambiar las dependencias o desmontar, lo que estaba en vuelo se cancela: si no,
    // cada cambio de filtro dejaba al backend terminando una consulta que nadie iba a
    // mirar (D-131). El error de la cancelación no llega a pintarse: `vigente` ya es falso.
    const cancelar = new AbortController();
    setEstado({ fase: "cargando" });
    cargar(cancelar.signal)
      .then((datos) => {
        if (vigente) setEstado({ fase: "listo", datos });
      })
      .catch((error: Error) => {
        if (!vigente) return;
        // Distinguir «no hay backend» de «el backend ha dicho que no» es la diferencia
        // entre una pantalla que ayuda y una que sólo dice que algo ha fallado. «No hay
        // backend» es sólo un fallo de red (`TypeError` de fetch); una respuesta que no
        // se entiende, o un error de la propia pantalla, es un error, no una caída.
        if (!(error instanceof ApiError)) {
          return setEstado(
            error instanceof TypeError && /fetch|network|load failed/i.test(error.message)
              ? { fase: "sin-backend" }
              : { fase: "error", error },
          );
        }
        // 401 y 403 no son «el backend ha fallado»: son «te falta una clave» y «esa
        // clave no es de este proyecto», y cada una tiene su pantalla y su salida.
        if (error.status === 401) return setEstado({ fase: "sin-clave", error });
        if (error.status === 403) return setEstado({ fase: "sin-permiso", error });
        setEstado({ fase: "error", error });
      });
    return () => {
      vigente = false;
      cancelar.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return estado;
}
