#!/usr/bin/env bash
set -Eeuo pipefail
BASE=${1:?base directory required}
RELEASE_SHA=${2:?release SHA required}
RELEASE="$BASE/releases/$RELEASE_SHA"
ENV_FILE="$BASE/shared/.env"
CURRENT="$BASE/current"

[[ -d "$RELEASE" ]] || { echo "Missing release directory: $RELEASE" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "Missing runtime environment: $ENV_FILE" >&2; exit 1; }
ln -sfn "$ENV_FILE" "$RELEASE/.env"

PREVIOUS=""
if [[ -L "$CURRENT" ]]; then
  PREVIOUS=$(readlink -f "$CURRENT" || true)
fi

rollback() {
  echo "Release health check failed; rolling back" >&2
  if [[ -n "$PREVIOUS" && -d "$PREVIOUS" ]]; then
    ln -sfn "$PREVIOUS" "$BASE/current.rollback"
    mv -Tf "$BASE/current.rollback" "$CURRENT"
    cd "$PREVIOUS"
    APP_VERSION=$(basename "$PREVIOUS") docker compose -p sibyl-trace up -d --remove-orphans || true
  else
    cd "$RELEASE"
    docker compose -p sibyl-trace down || true
    rm -f "$CURRENT"
  fi
  exit 1
}

cd "$RELEASE"
APP_VERSION="$RELEASE_SHA" docker compose -p sibyl-trace config -q
APP_VERSION="$RELEASE_SHA" docker compose -p sibyl-trace pull db tunnel
APP_VERSION="$RELEASE_SHA" docker compose -p sibyl-trace build --pull api worker backup

ln -sfn "$RELEASE" "$BASE/current.next"
mv -Tf "$BASE/current.next" "$CURRENT"
cd "$CURRENT"
APP_VERSION="$RELEASE_SHA" docker compose -p sibyl-trace up -d --remove-orphans || rollback

healthy=false
for _ in $(seq 1 30); do
  running=$(docker compose -p sibyl-trace ps --services --filter status=running)
  all_running=true
  for service in db api worker tunnel backup; do
    if ! grep -qx "$service" <<<"$running"; then
      all_running=false
      break
    fi
  done
  if [[ "$all_running" == true ]] \
    && docker compose -p sibyl-trace exec -T api python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" \
    && docker compose -p sibyl-trace exec -T backup test -f /tmp/ready; then
    healthy=true
    break
  fi
  sleep 4
done
[[ "$healthy" == true ]] || rollback

docker compose -p sibyl-trace ps
mapfile -t releases < <(
  find "$BASE/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
    | sort -nr | cut -d' ' -f2-
)
kept_old=0
for candidate in "${releases[@]}"; do
  if [[ "$candidate" == "$RELEASE" || "$candidate" == "$PREVIOUS" ]]; then
    continue
  fi
  kept_old=$((kept_old + 1))
  if (( kept_old > 2 )); then
    rm -rf "$candidate"
  fi
done
printf 'Activated release %s\n' "$RELEASE_SHA"
