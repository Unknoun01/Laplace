/**
 * En qué idioma se habla (Fase 5, D-147). Espejo de `laplace_backend/idioma.py`.
 *
 * Vive fuera de React a propósito: `format.ts` lo necesita para escribir una cifra, y
 * `api.ts` para pedirle al backend las frases del motor en el mismo idioma que la
 * pantalla. Cuando cambia, el proveedor de `i18n.tsx` vuelve a montar la página entera,
 * así que nadie se queda con cifras o frases del idioma anterior.
 */

export const IDIOMAS = ["es", "en", "pt", "fr", "zh"] as const;
export type Idioma = (typeof IDIOMAS)[number];

/** Cada idioma por su nombre en ese idioma: quien no lee español tiene que encontrar el suyo. */
export const NOMBRES: Record<Idioma, string> = {
  es: "Español",
  en: "English",
  pt: "Português",
  fr: "Français",
  zh: "中文",
};

/** Etiqueta BCP 47 completa, para `Intl` y para `<html lang>`. */
export const ETIQUETAS: Record<Idioma, string> = {
  es: "es-ES",
  en: "en-US",
  pt: "pt-BR",
  fr: "fr-FR",
  zh: "zh-CN",
};

const CLAVE = "laplace.idioma";

// El HTML estático se construye en español; hasta que se detecta, es el que vale.
let actual: Idioma = "es";

export function idiomaActual(): Idioma {
  return actual;
}

export function fijarIdioma(idioma: Idioma): void {
  actual = idioma;
}

/** `pt-BR` → `pt`; lo que no sea uno de los cinco, `null`. */
export function valido(etiqueta: string | null | undefined): Idioma | null {
  if (!etiqueta) return null;
  const base = etiqueta.trim().toLowerCase().replace("_", "-").split("-")[0];
  return (IDIOMAS as readonly string[]).includes(base) ? (base as Idioma) : null;
}

/**
 * El idioma con el que abrir: el que se eligió en este navegador, si se eligió; si no,
 * el primero de los del navegador que hablemos; si ninguno, inglés, que es el que más
 * gente sin español lee.
 */
export function detectar(): Idioma {
  try {
    const guardado = valido(localStorage.getItem(CLAVE));
    if (guardado) return guardado;
  } catch {
    // Sin almacenamiento (ventana privada estricta): se decide por el navegador.
  }
  const pedidos = typeof navigator === "undefined" ? [] : [...(navigator.languages ?? []), navigator.language];
  for (const etiqueta of pedidos) {
    const idioma = valido(etiqueta);
    if (idioma) return idioma;
  }
  return "en";
}

export function recordar(idioma: Idioma): void {
  try {
    localStorage.setItem(CLAVE, idioma);
  } catch {
    // No se recuerda, pero se usa: la próxima vez volverá a decidir el navegador.
  }
}
