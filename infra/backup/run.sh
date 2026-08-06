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
  output="/tmp/sibyl-${stamp}.sql.gz.enc"
  pg_dump -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl \
    | gzip -9 \
    | openssl enc -aes-256-cbc -salt -pbkdf2 \
        -pass env:BACKUP_ENCRYPTION_KEY -out "$output"
  aws --endpoint-url "$R2_ENDPOINT_URL" s3 cp "$output" \
    "s3://${R2_BUCKET}/daily/$(basename "$output")" --only-show-errors
  rm -f "$output"
  touch /tmp/ready
  sleep 86400
done
