FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install --production
# Force cache bust - updated 2026-04-01c
COPY . .
EXPOSE 3117
CMD ["node", "server.mjs"]
