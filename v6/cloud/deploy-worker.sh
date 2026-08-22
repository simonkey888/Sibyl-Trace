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
  --set-env-vars "DRY_RUN=true,SIBYL_V6_LIVE_ALLOWED=false,SIBYL_V6_RUN_UPSTREAM=0,SOURCE_SHA=$SOURCE_SHA,SIBYL_V6_EVIDENCE_BUCKET=$GCP_EVIDENCE_BUCKET" \
  --quiet

# Post-deploy structural proof: one worker, exact digest, runtime SA, no LIVE_ARMED.
desc_file="$(mktemp)"
trap 'rm -f "$desc_file"' EXIT
gcloud run worker-pools describe sibyl-v6-r1 \
  --project "$GCP_PROJECT_ID" \
  --region "$GCP_WORKER_REGION" \
  --format=json > "$desc_file"

python3 - "$desc_file" "$SOURCE_SHA" "$IMAGE_DIGEST" "$GCP_RUNTIME_SA" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

path, source_sha, digest, runtime_sa = sys.argv[1:]
obj = json.loads(Path(path).read_text(encoding="utf-8"))


def walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


raw = json.dumps(obj, sort_keys=True)
assert digest in raw, "worker pool is not pinned to requested immutable digest"
assert runtime_sa in raw, "wrong runtime service account"
assert source_sha in raw, "exact source SHA missing from deployed environment"

env = {}
for value in walk(obj):
    if isinstance(value, dict) and isinstance(value.get("name"), str) and "value" in value:
        env[value["name"]] = str(value["value"])
assert "LIVE_ARMED" not in env, "LIVE_ARMED must not be attached in R1"
assert env.get("DRY_RUN", "").casefold() == "true", "DRY_RUN missing/false"
assert env.get("SIBYL_V6_LIVE_ALLOWED", "").casefold() == "false", "LIVE_ALLOWED must be false"
assert env.get("SIBYL_V6_RUN_UPSTREAM") == "0", "R1 worker must not execute upstream orders"
assert env.get("SOURCE_SHA") == source_sha, "wrong SOURCE_SHA env"

instance_counts = []
for value in walk(obj):
    if not isinstance(value, dict):
        continue
    for key, child in value.items():
        if "manualInstanceCount" in str(key):
            try:
                instance_counts.append(int(child))
            except (TypeError, ValueError):
                pass
assert 1 in instance_counts, f"manual instance count is not exactly one: {instance_counts}"
print("WORKER_POOL_STRUCTURAL_GATE=PASS")
PY
