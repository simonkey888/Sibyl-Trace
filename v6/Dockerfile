# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS upstream
ARG UPSTREAM_SHA=e35ad881f88c7b5d60388461095ee11b7aa161c5
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*
COPY v6/upstream/repair_lock.py /tmp/sibyl-v6-repair-lock.py
RUN git clone --filter=blob:none https://github.com/limitless-labs-group/agents-starter.git /opt/agents-starter \
    && git -C /opt/agents-starter fetch --depth=1 origin "$UPSTREAM_SHA" \
    && git -C /opt/agents-starter checkout --detach "$UPSTREAM_SHA" \
    && test "$(git -C /opt/agents-starter rev-parse HEAD)" = "$UPSTREAM_SHA" \
    && test -d /opt/agents-starter/src/strategies/cross-market-mm \
    && printf '%s\n' "$UPSTREAM_SHA" > /opt/agents-starter/.sibyl-upstream-sha
WORKDIR /opt/agents-starter
RUN python3 /tmp/sibyl-v6-repair-lock.py package-lock.json .sibyl-dependency-provenance.json \
    && npm ci --ignore-scripts --no-audit --no-fund \
    && test "$(git rev-parse HEAD)" = "$UPSTREAM_SHA" \
    && node -e "const p=require('./node_modules/ox/package.json'); if(p.version!=='0.14.26') process.exit(7)" \
    && sha256sum package-lock.json | awk '{print $1}' > .sibyl-patched-lock-sha256

FROM node:22-bookworm-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/lib/sibyl-v6/evidence /var/lib/sibyl-v6/store \
    && chown -R node:node /var/lib/sibyl-v6
COPY --from=upstream /opt/agents-starter /opt/agents-starter
RUN git config --system --add safe.directory /opt/agents-starter \
    && test "$(git -C /opt/agents-starter rev-parse HEAD)" = "$(cat /opt/agents-starter/.sibyl-upstream-sha)"
COPY v6 /app/v6
ENV PYTHONPATH=/app/v6 \
    SIBYL_V6_UPSTREAM_ROOT=/opt/agents-starter \
    SIBYL_V6_EVIDENCE_DIR=/var/lib/sibyl-v6/evidence \
    SIBYL_V6_FILE_STORE_ROOT=/var/lib/sibyl-v6/store \
    SIBYL_V6_EVIDENCE_BACKEND=FILE \
    SIBYL_V6_VERIFIED_PAIRS=/app/v6/config/verified_pairs.json \
    SIBYL_V6_CYCLE_INTERVAL_SECONDS=60 \
    DRY_RUN=true \
    SIBYL_V6_LIVE_ALLOWED=false \
    SIBYL_V6_RUN_UPSTREAM=0
WORKDIR /app
USER node
ENTRYPOINT ["python3", "-m", "sibyl_v6.worker"]
