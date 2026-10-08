# syntax=docker/dockerfile:1.7
# Фронтенд «Взаимно»: сборка Vite (node) → статика в nginx с SPA-fallback и прокси /api в бэкенд.
FROM node:22.12-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
# API на том же origin (nginx проксирует /api), поэтому VITE_API_BASE_URL пуст
RUN npm run build

FROM nginx:1.27.5-alpine AS runtime
# Шаблон обрабатывается entrypoint-ом nginx: ${VZ_BACKEND_UPSTREAM} подставляется при старте
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY security-headers.conf /etc/nginx/security-headers.conf
COPY csp.conf /etc/nginx/csp.conf
COPY --from=build /app/dist /usr/share/nginx/html
ENV VZ_BACKEND_UPSTREAM=backend:8000 \
    VZ_REAL_IP_FROM=127.0.0.1 \
    NGINX_ENVSUBST_FILTER=VZ_
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
