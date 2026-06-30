# aria-app — Next.js 14 standalone. Frontend only; talks to server.mjs (/api) + brain (/api/aria).
# Build SHA injected via ARIA_BUILD_GIT_SHA (mirrors Dockerfile.web).
FROM node:22-slim AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3200
# Next standalone bundle (server.js + minimal node_modules) + static assets.
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
EXPOSE 3200
CMD ["node", "server.js"]
