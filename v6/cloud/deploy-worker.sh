#!/usr/bin/env bash
set -Eeuo pipefail

: "${GCP_PROJECT_ID:?required}"
: "${GCP_WORKER_REGION:?region must come from completed probe evidence}"
: "${GCP_ARTIFACT_REGION:?required}"
: "${GCP_ARTIFACT_REPO:=sibyl-v6}"
: "${GCP_IMAGE_NAME:=sibyl-v6}"
: "${GCP_RUNTIME_SA:=sibyl-v6-runtime@$GCP_PROJECT_ID.iam.gserviceaccount.com}"
: "${GCP_EVIDENCE_BUCKET:?required}"
: "${SOURCE_SHA:?exact 40-char source SHA required}"
: "${IMAGE_DIGEST:?sha256 digest required}"

[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ "$GCP_WORKER_REGION" =~ ^(us-east1|us-central1|southamerica-east1)$ ]]

IMAGE="${GCP_ARTIFACT_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${GCP_ARTIFACT_REPO}/${GCP_IMAGE_NAME}@${IMAGE_DIGEST}"

# R1 intentionally attaches NO trading/auth secret and no LIVE_ARMED.
gcloud run worker-pools deploy sibyl-v6-r1 \
  --project "$GCP_PROJECT_ID" \
  --region "$GCP_WORKER_REGION" \
  --image "$IMAGE" \
  --service-account "$GCP_RUNTIME_SA" \
  --instances 1 \
  --set-env-vars "DRY_RUN=true,SIBYL_V6_LIVE_ALLOWED=false,SIBYL_V6_RUN_UPSTREAM=0,SOURCE_SHA=$SOURCE_SHA,SIBYL_V6_EVIDENCE_BUCKET=$GCP_EVIDENCE_BUCKET"

# Post-deploy structural proof: one worker, exact digest, runtime SA, no LIVE_ARMED.
DESC="$(gcloud run worker-pools describe sibyl-v6-r1 --project "$GCP_PROJECT_ID" --region "$GCP_WORKER_REGION" --format=json)"
python3 - "$SOURCE_SHA" "$IMAGE_DIGEST" "$GCP_RUNTIME_SA" <<'PY' <<<"$DESC"
import json, sys
source_sha, digest, runtime_sa = sys.argv[1:]
obj = json.load(sys.stdin)
raw = json.dumps(obj, sort_keys=True)
assert digest in raw, 'worker pool is not pinned to requested immutable digest'
assert runtime_sa in raw, 'wrong runtime service account'
assert 'LIVE_ARMED' not in raw, 'LIVE_ARMED must not be attached in R1'
assert 'DRY_RUN' in raw and 'true' in raw.lower(), 'DRY_RUN missing'
assert source_sha in raw, 'exact source SHA missing from deployed environment'
print('WORKER_POOL_STRUCTURAL_GATE=PASS')
PY
