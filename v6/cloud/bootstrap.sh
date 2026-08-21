#!/usr/bin/env bash
set -Eeuo pipefail

: "${GCP_PROJECT_ID:?GCP_PROJECT_ID required}"
: "${GCP_ARTIFACT_REGION:=us-central1}"
: "${GCP_ARTIFACT_REPO:=sibyl-v6}"
: "${GCP_STATE_BUCKET:?GCP_STATE_BUCKET required}"
: "${GCP_EVIDENCE_BUCKET:?GCP_EVIDENCE_BUCKET required}"
: "${GCP_WIF_POOL:=sibyl-v6-github}"
: "${GCP_WIF_PROVIDER:=sibyl-v6-github-provider}"
: "${GCP_LIVE_ARMED_SECRET:=sibyl-v6-live-armed}"
: "${GITHUB_REPOSITORY:=simonkey888/Sibyl-Trace}"
: "${SIBYL_V6_APPLY:=NO}"
: "${SIBYL_V6_COST_AUTHORIZED_USD:=0}"

python3 - <<'PY'
import os
try:
    value = float(os.environ.get('SIBYL_V6_COST_AUTHORIZED_USD', '0'))
except ValueError:
    raise SystemExit('SIBYL_V6_COST_AUTHORIZED_USD must be numeric')
if os.environ.get('SIBYL_V6_APPLY') != 'YES' or value <= 0:
    raise SystemExit('REFUSING_MUTATION: explicit apply + positive cost authorization required')
PY

PROJECT_NUMBER="$(gcloud projects describe "$GCP_PROJECT_ID" --format='value(projectNumber)')"
BUILDER_SA="sibyl-v6-builder@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
RUNTIME_SA="sibyl-v6-runtime@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

# APIs required for the declared architecture only.
gcloud services enable \
  artifactregistry.googleapis.com \
  iamcredentials.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  sts.googleapis.com \
  --project "$GCP_PROJECT_ID"

for sa in sibyl-v6-builder sibyl-v6-runtime; do
  if ! gcloud iam service-accounts describe "$sa@$GCP_PROJECT_ID.iam.gserviceaccount.com" --project "$GCP_PROJECT_ID" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$sa" --project "$GCP_PROJECT_ID" --display-name "$sa"
  fi
done

if ! gcloud artifacts repositories describe "$GCP_ARTIFACT_REPO" --location "$GCP_ARTIFACT_REGION" --project "$GCP_PROJECT_ID" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$GCP_ARTIFACT_REPO" \
    --repository-format=docker \
    --location "$GCP_ARTIFACT_REGION" \
    --project "$GCP_PROJECT_ID"
fi

for bucket in "$GCP_STATE_BUCKET" "$GCP_EVIDENCE_BUCKET"; do
  if ! gcloud storage buckets describe "gs://$bucket" --project "$GCP_PROJECT_ID" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://$bucket" \
      --project "$GCP_PROJECT_ID" \
      --location "$GCP_ARTIFACT_REGION" \
      --uniform-bucket-level-access
  fi
  gcloud storage buckets update "gs://$bucket" --versioning
 done

# Runtime can only persist/read its dedicated buckets. No source/CI credentials.
for bucket in "$GCP_STATE_BUCKET" "$GCP_EVIDENCE_BUCKET"; do
  gcloud storage buckets add-iam-policy-binding "gs://$bucket" \
    --member "serviceAccount:$RUNTIME_SA" \
    --role roles/storage.objectAdmin
 done

# Builder may read non-secret evidence back from the evidence bucket so CI can
# select a region from exact probe artifacts. It cannot modify runtime evidence.
gcloud storage buckets add-iam-policy-binding "gs://$GCP_EVIDENCE_BUCKET" \
  --member "serviceAccount:$BUILDER_SA" \
  --role roles/storage.objectViewer

# Secret Manager is the only future secret store. R1 creates a secret container
# with NO secret version/value and grants no accessor role to builder or runtime.
if ! gcloud secrets describe "$GCP_LIVE_ARMED_SECRET" --project "$GCP_PROJECT_ID" >/dev/null 2>&1; then
  gcloud secrets create "$GCP_LIVE_ARMED_SECRET" \
    --project "$GCP_PROJECT_ID" \
    --replication-policy=automatic
fi
if gcloud secrets versions list "$GCP_LIVE_ARMED_SECRET" \
  --project "$GCP_PROJECT_ID" \
  --filter='state=ENABLED' \
  --format='value(name)' | grep -q .; then
  echo 'R1_LIVE_ARMED_SECRET_MUST_HAVE_NO_ENABLED_VERSION' >&2
  exit 1
fi

# Builder can push immutable images and deploy Worker Pools/Jobs, but never read
# Secret Manager values.
gcloud artifacts repositories add-iam-policy-binding "$GCP_ARTIFACT_REPO" \
  --location "$GCP_ARTIFACT_REGION" \
  --project "$GCP_PROJECT_ID" \
  --member "serviceAccount:$BUILDER_SA" \
  --role roles/artifactregistry.writer

gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
  --member "serviceAccount:$BUILDER_SA" \
  --role roles/run.developer >/dev/null

gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --project "$GCP_PROJECT_ID" \
  --member "serviceAccount:$BUILDER_SA" \
  --role roles/iam.serviceAccountUser >/dev/null

# WIF pool/provider: GitHub repository restricted by attribute condition.
if ! gcloud iam workload-identity-pools describe "$GCP_WIF_POOL" --location=global --project "$GCP_PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$GCP_WIF_POOL" --location=global --project "$GCP_PROJECT_ID"
fi
if ! gcloud iam workload-identity-pools providers describe "$GCP_WIF_PROVIDER" --workload-identity-pool "$GCP_WIF_POOL" --location=global --project "$GCP_PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "$GCP_WIF_PROVIDER" \
    --workload-identity-pool "$GCP_WIF_POOL" \
    --location global \
    --project "$GCP_PROJECT_ID" \
    --issuer-uri https://token.actions.githubusercontent.com \
    --attribute-mapping 'google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref' \
    --attribute-condition "assertion.repository=='$GITHUB_REPOSITORY'"
fi

gcloud iam service-accounts add-iam-policy-binding "$BUILDER_SA" \
  --project "$GCP_PROJECT_ID" \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${GCP_WIF_POOL}/attribute.repository/${GITHUB_REPOSITORY}" \
  --role roles/iam.workloadIdentityUser >/dev/null

# Prove the forbidden role is not directly attached to builder.
if gcloud projects get-iam-policy "$GCP_PROJECT_ID" --flatten='bindings[].members' \
  --filter="bindings.role:roles/secretmanager.secretAccessor AND bindings.members:serviceAccount:$BUILDER_SA" \
  --format='value(bindings.role)' | grep -q .; then
  echo 'BUILDER_SECRET_ACCESSOR_FORBIDDEN' >&2
  exit 1
fi

cat <<EOF
GCP_BOOTSTRAP=APPLIED
PROJECT_ID=$GCP_PROJECT_ID
BUILDER_SA=$BUILDER_SA
RUNTIME_SA=$RUNTIME_SA
ARTIFACT_REPO=$GCP_ARTIFACT_REGION-docker.pkg.dev/$GCP_PROJECT_ID/$GCP_ARTIFACT_REPO
WIF_PROVIDER=projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$GCP_WIF_POOL/providers/$GCP_WIF_PROVIDER
LIVE_ARMED_SECRET=$GCP_LIVE_ARMED_SECRET
LIVE_ARMED_ENABLED_VERSIONS=0
EOF
