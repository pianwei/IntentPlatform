FROM node:22.16.0-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/ ./
ARG BUILD_SCRIPT=build
RUN npm run ${BUILD_SCRIPT}

FROM nginx:1.28.0-alpine
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/console /usr/share/nginx/html/console
COPY --from=build /app/dist/agent-web /usr/share/nginx/html/runtime
EXPOSE 5173
