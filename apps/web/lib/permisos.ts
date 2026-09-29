"use client";

import { useEffect, useState } from "react";
import { type Me, getMe } from "./api";

/**
 * Qué puede hacer quien está mirando, en un proyecto (D-127).
 *
 * Sirve para no enseñar botones que el backend va a rechazar: a un lector no se le
 * ofrece «Lo he arreglado» para que luego le salga un 403. **No es la protección**: el
 * middleware comprueba el rol en cada escritura, y esto sólo evita ofrecer lo que no se
 * puede. Por eso, ante la duda —todavía cargando, o sin respuesta— se deja escribir: el
 * backend dirá que no si no se puede, y una pantalla sin botones por un fallo de red
 * sería peor.
 */
interface Permisos {
  escribir: boolean;
  administrar: boolean;
  rol: string | null;
}

const TODO: Permisos = { escribir: true, administrar: true, rol: null };
const ORDEN = ["lector", "miembro", "admin", "propietario"];

let pendiente: Promise<Me | null> | null = null;

function cargarMe(): Promise<Me | null> {
  if (!pendiente) pendiente = getMe().catch(() => null);
  return pendiente;
}

function permisosDe(me: Me | null, project: string): Permisos {
  // Local, sin respuesta, o una clave de API: la clave ya está atada a su proyecto.
  if (!me || me.mode === "local" || !me.user) return TODO;
  if (me.user.is_admin) return { ...TODO, rol: "propietario" };
  const rol = me.roles?.[project] ?? null;
  const nivel = rol ? ORDEN.indexOf(rol) : -1;
  return { escribir: nivel >= 1, administrar: nivel >= 2, rol };
}

export function usePermisos(project: string): Permisos {
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    let vigente = true;
    cargarMe().then((m) => vigente && setMe(m));
    return () => {
      vigente = false;
    };
  }, []);
  return permisosDe(me, project);
}
