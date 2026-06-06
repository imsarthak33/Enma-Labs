# syntax=docker/dockerfile:1.7
# =============================================================================
# enma-gateway — multi-stage build.
#   * base → pinned node-slim
#   * dev  → full deps + nodemon-style --watch entrypoint
#   * prod → only production deps, runs as non-root
# =============================================================================

ARG NODE_VERSION=20-bookworm-slim

# ---------- base -------------------------------------------------------------
FROM node:${NODE_VERSION} AS base

ENV NODE_ENV=production \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_UPDATE_NOTIFIER=false

WORKDIR /app

# ---------- dev --------------------------------------------------------------
FROM base AS dev

ENV NODE_ENV=development

COPY package.json package-lock.json* ./
RUN npm install --include=dev

COPY . .

EXPOSE 3000
CMD ["node", "--watch", "src/index.js"]

# ---------- prod -------------------------------------------------------------
FROM base AS prod

COPY package.json package-lock.json* ./
RUN npm ci --omit=dev

COPY src ./src

# Non-root.
RUN useradd --create-home --uid 10001 enma \
    && chown -R enma:enma /app
USER enma

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))" \
    || exit 1

CMD ["node", "src/index.js"]
