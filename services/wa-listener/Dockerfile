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
