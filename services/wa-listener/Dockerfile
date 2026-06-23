# R-F833 — Dockerfile for the `aria-wa` Fly app (Baileys WhatsApp listener).
#
# NOTE (R-F1805 reverted 2026-06-23): a multi-stage refactor (audit #9, drop the
# build toolchain from the runtime image) produced a v61 image MISSING
# /app/lib/observability/errorTracker.mjs despite `COPY lib/ ./lib/`, crash-looping
# the listener on boot. The same multi-stage pattern works for aria-web, so this is
# a wa-build-specific gremlin (Depot/buildkit cache or context) that cannot be
# reproduced without a local Docker build. Reverted to the proven single-stage to
# restore service; audit #9 is deferred until it can be validated with a real build.
FROM node:20-alpine
RUN apk add --no-cache python3 make g++
WORKDIR /app
COPY services/wa-listener/package.json ./services/wa-listener/
RUN cd services/wa-listener && npm install --only=production
COPY lib/ ./lib/
COPY services/wa-listener/aria_wa_listener.mjs ./services/wa-listener/
RUN mkdir -p /data/wa-listener-auth
VOLUME ["/data/wa-listener-auth"]
EXPOSE 5070
CMD ["node", "services/wa-listener/aria_wa_listener.mjs"]
