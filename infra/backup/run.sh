#!/bin/sh
set -eu
: "${POSTGRES_DB:?}" "${POSTGRES_USER:?}" "${POSTGRES_PASSWORD:?}"
: "${R2_ENDPOINT_URL:?}" "${R2_BUCKET:?}" "${R2_ACCESS_KEY_ID:?}" "${R2_SECRET_ACCESS_KEY:?}"
: "${BACKUP_ENCRYPTION_KEY:?}"
export PGPASSWORD="$POSTGRES_PASSWORD"
export AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION=auto
while true; do
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  name="sibyl-${stamp}.dump.enc"
  output="/tmp/${name}"
  authenticator="${output}.hmac"
  pg_dump -Fc -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl \
    | openssl enc -aes-256-cbc -salt -pbkdf2 \
        -pass env:BACKUP_ENCRYPTION_KEY -out "$output"
  python3 - "$output" "$authenticator" <<'PY'
import hashlib
import hmac
import os
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
key = os.environ["BACKUP_ENCRYPTION_KEY"].encode()
digest = hmac.new(key, source.read_bytes(), hashlib.sha256).hexdigest()
target.write_text(f"{digest}  {source.name}\n", encoding="ascii")
PY
  aws --endpoint-url "$R2_ENDPOINT_URL" s3 cp "$output" \
    "s3://${R2_BUCKET}/daily/${name}" --only-show-errors
  aws --endpoint-url "$R2_ENDPOINT_URL" s3 cp "$authenticator" \
    "s3://${R2_BUCKET}/daily/${name}.hmac" --only-show-errors
  rm -f "$output" "$authenticator"
  touch /tmp/ready
  sleep 86400
done
