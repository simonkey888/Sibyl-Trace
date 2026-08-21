from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/token"
)


def _access_token(timeout: float = 3.0) -> str:
    req = urllib.request.Request(_METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not token:
        raise RuntimeError("GCP_METADATA_ACCESS_TOKEN_MISSING")
    return str(token)


def upload_bytes(bucket: str, object_name: str, payload: bytes, *, token: str | None = None) -> None:
    if not bucket or not object_name:
        raise ValueError("bucket and object_name required")
    access = token or _access_token()
    url = (
        "https://storage.googleapis.com/upload/storage/v1/b/"
        + urllib.parse.quote(bucket, safe="")
        + "/o?uploadType=media&name="
        + urllib.parse.quote(object_name, safe="")
    )
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {access}",
            "Content-Type": "application/octet-stream",
        },
    )
    with urllib.request.urlopen(req, timeout=20.0) as response:
        if int(response.status) not in (200, 201):
            raise RuntimeError(f"GCS_UPLOAD_STATUS_{response.status}")


def download_bytes(bucket: str, object_name: str, *, token: str | None = None) -> bytes:
    access = token or _access_token()
    url = (
        "https://storage.googleapis.com/storage/v1/b/"
        + urllib.parse.quote(bucket, safe="")
        + "/o/"
        + urllib.parse.quote(object_name, safe="")
        + "?alt=media"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access}"})
    with urllib.request.urlopen(req, timeout=20.0) as response:
        return response.read()


def persist_evidence_directory(directory: Path, *, source_sha: str) -> list[str]:
    """Periodic durable checkpoint only; never a high-frequency append log."""
    bucket = os.environ.get("SIBYL_V6_EVIDENCE_BUCKET", "").strip()
    if not bucket:
        return []
    uploaded: list[str] = []
    prefix = f"evidence/{source_sha}"
    for path in sorted(directory.glob("*.json")):
        object_name = f"{prefix}/{path.name}"
        upload_bytes(bucket, object_name, path.read_bytes())
        uploaded.append(object_name)
    return uploaded
