#!/usr/bin/env bash
set -euo pipefail
: "${1:?encrypted backup path required}"
: "${BACKUP_ENCRYPTION_KEY:?}" "${POSTGRES_DB:?}" "${POSTGRES_USER:?}" "${POSTGRES_PASSWORD:?}"
export PGPASSWORD="$POSTGRES_PASSWORD"
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$1" \
  | gunzip \
  | docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
