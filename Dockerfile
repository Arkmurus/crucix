FROM node:22-alpine AS builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npx ng build --configuration production

FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install --production
COPY . .
COPY --from=builder /app/frontend/dist ./frontend/dist
EXPOSE 3117
CMD ["node", "server.mjs"]
