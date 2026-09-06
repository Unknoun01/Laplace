/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Necesario para la imagen de Docker: empaqueta solo lo que hace falta.
  output: "standalone",
};

export default nextConfig;
