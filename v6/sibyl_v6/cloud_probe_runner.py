from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .checkpoint import upload_bytes
from .probe import run_probe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()

    source_sha = os.environ.get("SOURCE_SHA", "").strip()
    bucket = os.environ.get("SIBYL_V6_EVIDENCE_BUCKET", "").strip()
    if not source_sha:
        raise SystemExit("SOURCE_SHA_REQUIRED")
    if not bucket:
        raise SystemExit("SIBYL_V6_EVIDENCE_BUCKET_REQUIRED")

    payload = run_probe(args.region, args.repetitions)
    payload["source_sha"] = source_sha
    payload["execution_context"] = "DISPOSABLE_CLOUD_RUN_JOB"
    raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    object_name = f"evidence/{source_sha}/region-probes/{args.region}.json"
    upload_bytes(bucket, object_name, raw)
    print(
        json.dumps(
            {
                "event": "v6_region_probe_persisted",
                "region": args.region,
                "object": object_name,
                "summary": payload["summary"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
