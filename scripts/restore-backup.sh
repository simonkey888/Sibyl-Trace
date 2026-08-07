#!/usr/bin/env bash
set -Eeuo pipefail
BACKUP=${1:?encrypted backup path required}
AUTHENTICATOR=${2:-${BACKUP}.hmac}
: "${BACKUP_ENCRYPTION_KEY:?}" "${POSTGRES_DB:?}" "${POSTGRES_USER:?}" "${POSTGRES_PASSWORD:?}"
[[ -f "$BACKUP" ]] || { echo "Missing backup: $BACKUP" >&2; exit 1; }
[[ -f "$AUTHENTICATOR" ]] || { echo "Missing authenticator: $AUTHENTICATOR" >&2; exit 1; }

backup_dir=$(cd "$(dirname "$BACKUP")" && pwd)
backup_name=$(basename "$BACKUP")
authenticator_dir=$(cd "$(dirname "$AUTHENTICATOR")" && pwd)
if [[ "$authenticator_dir" != "$backup_dir" ]]; then
  echo "Backup and authenticator must be in the same directory" >&2
  exit 1
fi

python3 - "$BACKUP" "$AUTHENTICATOR" <<'PY'
import hashlib
import hmac
import os
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
authenticator = pathlib.Path(sys.argv[2])
parts = authenticator.read_text(encoding="ascii").strip().split(maxsplit=1)
if len(parts) != 2 or parts[1] != source.name:
    raise SystemExit("invalid backup authenticator format or filename")
expected = parts[0]
actual = hmac.new(
    os.environ["BACKUP_ENCRYPTION_KEY"].encode(),
    source.read_bytes(),
    hashlib.sha256,
).hexdigest()
if not hmac.compare_digest(expected, actual):
    raise SystemExit("backup HMAC verification failed")
print(f"{source.name}: HMAC verified")
PY

tmp_dump=$(mktemp)
trap 'rm -f "$tmp_dump"' EXIT
openssl enc -d -aes-256-cbc -pbkdf2 \
  -pass env:BACKUP_ENCRYPTION_KEY \
  -in "$BACKUP" \
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
