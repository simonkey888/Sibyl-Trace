#!/usr/bin/env bash
set -Eeuo pipefail
BACKUP=${1:?encrypted backup path required}
CHECKSUM=${2:-${BACKUP}.sha256}
: "${BACKUP_ENCRYPTION_KEY:?}" "${POSTGRES_DB:?}" "${POSTGRES_USER:?}" "${POSTGRES_PASSWORD:?}"
[[ -f "$BACKUP" ]] || { echo "Missing backup: $BACKUP" >&2; exit 1; }
[[ -f "$CHECKSUM" ]] || { echo "Missing checksum: $CHECKSUM" >&2; exit 1; }

backup_dir=$(cd "$(dirname "$BACKUP")" && pwd)
backup_name=$(basename "$BACKUP")
checksum_name=$(basename "$CHECKSUM")
if [[ "$(cd "$(dirname "$CHECKSUM")" && pwd)" != "$backup_dir" ]]; then
  echo "Backup and checksum must be in the same directory" >&2
  exit 1
fi
(cd "$backup_dir" && sha256sum -c "$checksum_name")

tmp_dump=$(mktemp)
trap 'rm -f "$tmp_dump"' EXIT
openssl enc -d -aes-256-cbc -pbkdf2 \
  -pass env:BACKUP_ENCRYPTION_KEY \
  -in "$backup_dir/$backup_name" \
  -out "$tmp_dump"

export PGPASSWORD="$POSTGRES_PASSWORD"
docker compose exec -T db pg_restore \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  --clean \
  --if-exists \
  --no-owner \
  --no-acl \
  --exit-on-error \
  --single-transaction < "$tmp_dump"
