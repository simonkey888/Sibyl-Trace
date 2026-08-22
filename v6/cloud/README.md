# Sibyl V6 R1 cloud contract

Target: GitHub Actions -> Google Workload Identity Federation -> Artifact Registry immutable digest -> Cloud Run Worker Pool (exactly one worker). Runtime durable checkpoints/evidence use Cloud Storage JSON API. High-frequency events are structured stdout for Cloud Logging; GCS FUSE is forbidden.

## Identity split

- `sibyl-v6-builder`: impersonated only through GitHub OIDC/WIF. May build/push Artifact Registry images and deploy/update the V6 worker pool. It may act-as the runtime service account but MUST NOT have Secret Manager Secret Accessor and MUST NOT receive trading secret values.
- `sibyl-v6-runtime`: assigned to the worker pool. It has only the storage permissions needed for the V6 state/evidence buckets. Future trading secrets may be attached to this identity only after a later audited LIVE order; R1 attaches none.
- Runtime receives no GitHub token/credential and therefore cannot modify source or CI.

No service-account JSON key is created or accepted.

## R1 deployment law

R1 deployment must set:

```text
DRY_RUN=true
SIBYL_V6_LIVE_ALLOWED=false
SIBYL_V6_RUN_UPSTREAM=0
```

`LIVE_ARMED` is neither committed nor attached. The application wrapper additionally strips trading credentials before delegating upstream. Thus the deployed R1 worker is read-only/public-feed research even if its environment is misconfigured upstream.

## Durable data

- State bucket: periodic state/checkpoint objects only.
- Evidence bucket: immutable/exact-SHA JSON evidence/checkpoints.
- Cloud Logging: all high-frequency structured stdout.
- No bucket mount and no Cloud Storage FUSE append path.

## Region selection

Deployment region is not hard-coded. `sibyl_v6.probe` must first execute from disposable compute in `us-east1`, `us-central1`, and `southamerica-east1`, recording repeated TCP/TLS/TTFB and WS-upgrade samples. HTTP 451 is evidence and never triggers a bypass. Region selection is forbidden until that artifact exists.

## Apply guard

`bootstrap.sh` refuses resource mutation unless `SIBYL_V6_APPLY=YES` and a positive `SIBYL_V6_COST_AUTHORIZED_USD` are both explicitly supplied. This prevents a repository commit or normal CI push from creating billable resources. Read-only inventory is performed separately by `sibyl-v6-gcp-observe.yml`.
