# Contexto de build: la raiz del monorepo.
FROM node:22-alpine AS deps
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm install --no-audit --no-fund

FROM node:22-alpine AS builder
WORKDIR /app
# Con `output: standalone`, Next resuelve las reescrituras durante el build y las
# congela en el servidor que genera. El destino tiene que estar aqui, no en el runtime.
ARG LAPLACE_API_URL=http://backend:8000
ENV LAPLACE_API_URL=$LAPLACE_API_URL
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
ENV PORT=3000 HOSTNAME=0.0.0.0
CMD ["node", "server.js"]
