/** @type {import('next').NextConfig} */

// `LAPLACE_EXPORT=1 npm run build` produce HTML estático en `out/`, que es lo que
// `laplace ui` sirve desde Python sin necesitar Node (D-069). Sin esa variable, el
// build es el de siempre para el contenedor de la nube. Es el mismo código: lo único
// que cambia es la forma del artefacto.
const exportar = Boolean(process.env.LAPLACE_EXPORT);

const backend = process.env.LAPLACE_API_URL ?? "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  output: exportar ? "export" : "standalone",
  // Con `export`, cada ruta cae en su propio `index.html`, que es lo que un servidor
  // de ficheros sabe resolver sin reglas.
  trailingSlash: exportar,
  // En la nube el navegador habla siempre con el mismo origen y Next hace de puente:
  // así la interfaz no lleva ninguna URL de backend incrustada en el build.
  async rewrites() {
    if (exportar) return [];
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/health", destination: `${backend}/health` },
    ];
  },
};

export default nextConfig;
