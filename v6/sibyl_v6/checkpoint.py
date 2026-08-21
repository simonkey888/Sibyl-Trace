from __future__ import annotations

from pathlib import Path

from .evidence_store import (
    GCSEvidenceStore,
    evidence_store_from_env,
    gcp_metadata_access_token,
    persist_directory,
)


def _access_token(timeout: float = 3.0) -> str:
    return gcp_metadata_access_token(timeout)


def upload_bytes(bucket: str, object_name: str, payload: bytes, *, token: str | None = None) -> None:
    provider = (lambda: token) if token is not None else gcp_metadata_access_token
    GCSEvidenceStore(bucket, token_provider=provider).put_bytes(object_name, payload)


def download_bytes(bucket: str, object_name: str, *, token: str | None = None) -> bytes:
    provider = (lambda: token) if token is not None else gcp_metadata_access_token
    return GCSEvidenceStore(bucket, token_provider=provider).get_bytes(object_name)


def persist_evidence_directory(directory: Path, *, source_sha: str) -> list[str]:
    """Persist via the configured backend; FILE is local-runtime evidence only."""
    store = evidence_store_from_env()
    return persist_directory(store, directory, source_sha=source_sha)
