# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS upstream
ARG UPSTREAM_SHA=e35ad881f88c7b5d60388461095ee11b7aa161c5
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --filter=blob:none https://github.com/limitless-labs-group/agents-starter.git /opt/agents-starter \
    && git -C /opt/agents-starter fetch --depth=1 origin "$UPSTREAM_SHA" \
    && git -C /opt/agents-starter checkout --detach "$UPSTREAM_SHA" \
    && test "$(git -C /opt/agents-starter rev-parse HEAD)" = "$UPSTREAM_SHA" \
    && test -d /opt/agents-starter/src/strategies/cross-market-mm \
    && printf '%s\n' "$UPSTREAM_SHA" > /opt/agents-starter/.sibyl-upstream-sha
WORKDIR /opt/agents-starter
RUN npm ci --ignore-scripts --no-audit --no-fund

FROM node:22-bookworm-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/lib/sibyl-v6/evidence \
    && chown -R node:node /var/lib/sibyl-v6
COPY --from=upstream /opt/agents-starter /opt/agents-starter
COPY v6 /app/v6
ENV PYTHONPATH=/app/v6 \
    SIBYL_V6_UPSTREAM_ROOT=/opt/agents-starter \
    SIBYL_V6_EVIDENCE_DIR=/var/lib/sibyl-v6/evidence \
    SIBYL_V6_VERIFIED_PAIRS=/app/v6/config/verified_pairs.json \
    DRY_RUN=true \
    SIBYL_V6_LIVE_ALLOWED=false
WORKDIR /app
USER node
ENTRYPOINT ["python3", "-m", "sibyl_v6.runner"]
