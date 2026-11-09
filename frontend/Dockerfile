# AutoApply AI dashboard (Next.js standalone).
# Build from the repository root:
#   docker build -f frontend/Dockerfile --build-arg BACKEND_URL=http://api:8000 -t autoapply-frontend .
FROM node:20-alpine AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:20-alpine AS build
WORKDIR /app
# The /api proxy target is compiled into the standalone server at build time.
ARG BACKEND_URL=http://api:8000
ARG NEXT_PUBLIC_WS_URL=
ENV BACKEND_URL=$BACKEND_URL \
    NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ .
RUN npm run build

FROM node:20-alpine AS run
WORKDIR /app
# Also needed at runtime by the /api/health route (the rewrites are compiled in at build time).
ARG BACKEND_URL=http://api:8000
ENV BACKEND_URL=$BACKEND_URL \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -S app && adduser -S app -G app
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
COPY --from=build --chown=app:app /app/public ./public
USER app
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:3000/ >/dev/null || exit 1
CMD ["node", "server.js"]
