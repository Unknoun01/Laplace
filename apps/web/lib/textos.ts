/**
 * Los textos de la interfaz, en el idioma actual (D-147).
 *
 * `mensajes/es.ts` es el catálogo de origen y define las claves; los otros cuatro están
 * tipados contra él, así que una clave que falte en un idioma no compila. Los huecos van
 * entre llaves —`{n}`, `{proyecto}`— y `test_textos_web.py` comprueba que cada traducción
 * tiene exactamente los mismos que el español: un `{n}` olvidado en chino dejaría la
 * cifra fuera de la frase sin que nada falle.
 *
 * Sin librería: las pantallas son componentes de cliente sobre HTML estático, y lo que
 * hace falta —buscar una clave, rellenar huecos y elegir singular o plural— son pocas
 * líneas. `next-intl` resuelve sobre todo rutas por idioma y render en servidor, que
 * aquí no hay.
 */

import { ETIQUETAS, idiomaActual, type Idioma } from "./idioma";
import { en } from "./mensajes/en";
import { es, type Clave, type Mensajes } from "./mensajes/es";
import { fr } from "./mensajes/fr";
import { pt } from "./mensajes/pt";
import { zh } from "./mensajes/zh";

export type { Clave } from "./mensajes/es";

export const CATALOGOS: Record<Idioma, Mensajes> = { es, en, pt, fr, zh };

type Valores = Record<string, string | number>;

function rellenar(texto: string, valores?: Valores): string {
  if (!valores) return texto;
  return texto.replace(/\{(\w+)\}/g, (entero, nombre: string) =>
    nombre in valores ? String(valores[nombre]) : entero,
  );
}

/** El texto de `clave` en el idioma actual, con sus huecos rellenos. */
export function t(clave: Clave, valores?: Valores): string {
  return rellenar(CATALOGOS[idiomaActual()][clave], valores);
}

/** Las claves con forma de plural: `x_one` y `x_other`. */
type Plural<K> = K extends `${infer B}_other` ? B : never;
export type ClavePlural = Plural<Clave>;

/**
 * Singular o plural según `n`, con las reglas del idioma (`Intl.PluralRules`): en chino
 * no hay plural y en francés el cero va en singular. `{n}` se rellena con `n` tal cual;
 * quien quiera la cifra formateada la pasa en `valores`.
 */
export function tn(base: ClavePlural, n: number, valores?: Valores): string {
  const forma = new Intl.PluralRules(ETIQUETAS[idiomaActual()]).select(n);
  const catalogo = CATALOGOS[idiomaActual()] as Record<string, string>;
  const texto = catalogo[`${base}_${forma}`] ?? catalogo[`${base}_other`];
  return rellenar(texto, { n, ...valores });
}
