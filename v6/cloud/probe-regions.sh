#!/usr/bin/env bash
set -Eeuo pipefail

: "${GCP_PROJECT_ID:?GCP_PROJECT_ID required}"
: "${GCP_RUNTIME_SERVICE_ACCOUNT:?GCP_RUNTIME_SERVICE_ACCOUNT required}"
: "${GCP_EVIDENCE_BUCKET:?GCP_EVIDENCE_BUCKET required}"
: "${IMAGE_URI:?IMAGE_URI required}"
: "${IMAGE_DIGEST:?IMAGE_DIGEST required}"
: "${SOURCE_SHA:?SOURCE_SHA required}"

if [[ ! "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "IMMUTABLE_IMAGE_DIGEST_REQUIRED" >&2
  exit 2
fi
if [[ "$IMAGE_URI" == *@* ]]; then
  echo "IMAGE_URI_MUST_NOT_ALREADY_CONTAIN_DIGEST" >&2
  exit 2
fi

IMAGE_REF="${IMAGE_URI}@${IMAGE_DIGEST}"
OUT="${REGION_PROBE_DIR:-/tmp/sibyl-v6-region-probes}"
mkdir -p "$OUT"
regions=(us-east1 us-central1 southamerica-east1)

cleanup_job() {
  local job="$1" region="$2"
  gcloud run jobs delete "$job" \
    --project "$GCP_PROJECT_ID" \
    --region "$region" \
    --quiet >/dev/null 2>&1 || true
}

for region in "${regions[@]}"; do
  short_sha="${SOURCE_SHA:0:10}"
  job="sibyl-v6-probe-${region//[!a-z0-9-]/-}-${short_sha}"
  trap 'cleanup_job "$job" "$region"' EXIT
  cleanup_job "$job" "$region"

  gcloud run jobs deploy "$job" \
    --project "$GCP_PROJECT_ID" \
    --region "$region" \
    --image "$IMAGE_REF" \
    --service-account "$GCP_RUNTIME_SERVICE_ACCOUNT" \
    --tasks 1 \
    --max-retries 0 \
    --task-timeout 180s \
    --command python3 \
    --args=-m,sibyl_v6.cloud_probe_runner,--region,"$region",--repetitions,5 \
    --set-env-vars="SOURCE_SHA=$SOURCE_SHA,SIBYL_V6_EVIDENCE_BUCKET=$GCP_EVIDENCE_BUCKET" \
    --quiet

  gcloud run jobs execute "$job" \
    --project "$GCP_PROJECT_ID" \
    --region "$region" \
    --wait \
    --quiet

  gcloud storage cp \
    "gs://${GCP_EVIDENCE_BUCKET}/evidence/${SOURCE_SHA}/region-probes/${region}.json" \
    "$OUT/${region}.json" \
    --quiet

  cleanup_job "$job" "$region"
  trap - EXIT
done

PYTHONPATH=v6 python3 -m sibyl_v6.select_region \
  "$OUT/us-east1.json" \
  "$OUT/us-central1.json" \
  "$OUT/southamerica-east1.json" \
  --output "$OUT/selection.json"

cat "$OUT/selection.json"
