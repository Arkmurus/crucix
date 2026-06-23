# R-F833 — Dockerfile for the `aria-wa` Fly app (Baileys WhatsApp listener).
#
# R-F1805 (2026-06-23, aria-wa audit #9) — MULTI-STAGE build. The toolchain
# (python3/make/g++) is only needed to compile any native npm modules; keeping
# it in the deployed image bloats the attack surface. Stage 1 builds; stage 2
# ships only the node runtime + built modules + app code. No compilers, and no
# git in production (the listener never shells out to git).

# ── Stage 1: builder (has the toolchain) ──────────────────────────────────────
FROM node:20-alpine AS builder
RUN apk add --no-cache python3 make g++
WORKDIR /app
COPY services/wa-listener/package.json ./services/wa-listener/
RUN cd services/wa-listener && npm install --only=production

# ── Stage 2: runtime (node only — NO compiler toolchain) ──────────────────────
FROM node:20-alpine AS runtime
WORKDIR /app
# Built node_modules from the builder (native modules, if any, compiled against
# the identical node:20-alpine/musl base → ABI-compatible).
COPY --from=builder /app/services/wa-listener/node_modules ./services/wa-listener/node_modules
COPY services/wa-listener/package.json ./services/wa-listener/
COPY lib/ ./lib/
COPY services/wa-listener/aria_wa_listener.mjs ./services/wa-listener/
RUN mkdir -p /data/wa-listener-auth
VOLUME ["/data/wa-listener-auth"]
EXPOSE 5070
CMD ["node", "services/wa-listener/aria_wa_listener.mjs"]
