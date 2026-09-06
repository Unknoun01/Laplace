import type { Config } from "tailwindcss";

const config: Config = {
  // `lib` tiene que estar: la paleta por tipo de span vive en lib/format.ts y, sin
  // incluirlo, Tailwind no genera esas clases y los colores salen transparentes.
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
