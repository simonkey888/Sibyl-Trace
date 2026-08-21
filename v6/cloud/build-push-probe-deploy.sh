#!/usr/bin/env bash
set -Eeuo pipefail

: "${GCP_PROJECT_ID:?required}"
: "${GCP_ARTIFACT_REGION:?required}"
: "${GCP_ARTIFACT_REPO:=sibyl-v6}"
: "${GCP_IMAGE_NAME:=sibyl-v6}"
: "${GCP_RUNTIME_SERVICE_ACCOUNT:?required}"
: "${GCP_EVIDENCE_BUCKET:?required}"
: "${SOURCE_SHA:?required}"

[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$(git rev-parse HEAD)" = "$SOURCE_SHA"

host="${GCP_ARTIFACT_REGION}-docker.pkg.dev"
image_uri="${host}/${GCP_PROJECT_ID}/${GCP_ARTIFACT_REPO}/${GCP_IMAGE_NAME}"
image_tag="${image_uri}:${SOURCE_SHA}"
out="${SIBYL_V6_CLOUD_EVIDENCE_DIR:-/tmp/sibyl-v6-cloud-evidence}"
mkdir -p "$out"

gcloud auth configure-docker "$host" --quiet
docker build --pull --tag "$image_tag" --file v6/Dockerfile .

push_log="$out/docker-push.txt"
docker push "$image_tag" | tee "$push_log"
image_digest="$(sed -nE 's/.*digest: (sha256:[0-9a-f]{64}).*/\1/p' "$push_log" | tail -n 1)"
if [[ ! "$image_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "REMOTE_IMAGE_DIGEST_NOT_RESOLVED" >&2
  exit 3
fi
# Remote registry must serve the exact content address before any cloud compute
# uses it. This is stronger than trusting a mutable tag.
docker manifest inspect "${image_uri}@${image_digest}" >/dev/null
printf '%s\n' "$image_digest" > "$out/image-digest.txt"
printf '%s\n' "$image_uri" > "$out/image-uri.txt"

GCP_RUNTIME_SERVICE_ACCOUNT="$GCP_RUNTIME_SERVICE_ACCOUNT" \
GCP_EVIDENCE_BUCKET="$GCP_EVIDENCE_BUCKET" \
IMAGE_URI="$image_uri" \
IMAGE_DIGEST="$image_digest" \
SOURCE_SHA="$SOURCE_SHA" \
REGION_PROBE_DIR="$out/region-probes" \
  bash v6/cloud/probe-regions.sh

selected_region="$(python3 - "$out/region-probes/selection.json" <<'PY'
import json, sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
assert payload['status'] == 'SELECTED', payload
print(payload['selected_region'])
PY
)"

GCP_WORKER_REGION="$selected_region" \
GCP_ARTIFACT_REGION="$GCP_ARTIFACT_REGION" \
GCP_ARTIFACT_REPO="$GCP_ARTIFACT_REPO" \
GCP_IMAGE_NAME="$GCP_IMAGE_NAME" \
GCP_RUNTIME_SA="$GCP_RUNTIME_SERVICE_ACCOUNT" \
GCP_EVIDENCE_BUCKET="$GCP_EVIDENCE_BUCKET" \
SOURCE_SHA="$SOURCE_SHA" \
IMAGE_DIGEST="$image_digest" \
  bash v6/cloud/deploy-worker.sh

gcloud run worker-pools describe sibyl-v6-r1 \
  --project "$GCP_PROJECT_ID" \
  --region "$selected_region" \
  --format=json > "$out/worker-pool.json"

# The continuous worker must publish exact-head runtime evidence. Poll only the
# durable checkpoint object; high-frequency data remains structured stdout.
runtime_object="gs://${GCP_EVIDENCE_BUCKET}/evidence/${SOURCE_SHA}/runtime-summary.json"
runtime_file="$out/runtime-summary.json"
for _ in $(seq 1 18); do
  if gcloud storage cp "$runtime_object" "$runtime_file" --quiet >/dev/null 2>&1; then
    if python3 - "$runtime_file" "$SOURCE_SHA" <<'PY'
import json, sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
assert payload['source_sha'] == sys.argv[2]
assert payload['LIVE'] == 'NO'
assert payload['REAL_ORDERS'] == 0
assert payload['CAPITAL_MOVED_USD'] == '0'
assert payload['LIVE_PREFLIGHT'] == 'NOT_RUN'
PY
    then
      break
    fi
  fi
  sleep 10
done

test -s "$runtime_file" || { echo "RUNTIME_EVIDENCE_NOT_OBSERVED" >&2; exit 5; }

python3 - "$out" "$SOURCE_SHA" "$image_digest" "$selected_region" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
source_sha, digest, region = sys.argv[2:]
selection = json.loads((root / 'region-probes/selection.json').read_text(encoding='utf-8'))
runtime = json.loads((root / 'runtime-summary.json').read_text(encoding='utf-8'))
summary = {
    'schema_version': 'SIBYL_V6_CLOUD_DEPLOYMENT_EVIDENCE_V1',
    'source_sha': source_sha,
    'image_digest': digest,
    'selected_region': region,
    'region_selection': selection,
    'worker_pool': 'sibyl-v6-r1',
    'worker_instances': 1,
    'runtime_summary': runtime,
    'LIVE': 'NO',
    'REAL_ORDERS': 0,
    'CAPITAL_MOVED_USD': '0',
}
(root / 'cloud-deployment-summary.json').write_text(
    json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8'
)
print(json.dumps(summary, sort_keys=True))
PY
