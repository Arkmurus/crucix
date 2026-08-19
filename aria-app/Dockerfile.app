# aria-app — Next.js 15 standalone. Frontend only; proxies /api/* to server.mjs.
# Build SHA injected via ARIA_BUILD_GIT_SHA (mirrors Dockerfile.web).
FROM node:22-slim AS builder
WORKDIR /app
# BACKEND_URL is baked into the API-proxy rewrites at build time (non-secret 6PN addr).
ARG BACKEND_URL=http://aria-web.internal:3117
ARG ARIA_BUILD_GIT_SHA=UNKNOWN-BUILD
ENV BACKEND_URL=$BACKEND_URL \
    ARIA_BUILD_GIT_SHA=$ARIA_BUILD_GIT_SHA
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-slim AS runner
WORKDIR /app
ARG BACKEND_URL=http://aria-web.internal:3117
ARG ARIA_BUILD_GIT_SHA=UNKNOWN-BUILD
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3200 \
    BACKEND_URL=$BACKEND_URL \
    ARIA_BUILD_GIT_SHA=$ARIA_BUILD_GIT_SHA
# Next standalone bundle (server.js + minimal node_modules) + static assets.
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
EXPOSE 3200
CMD ["node", "server.js"]
