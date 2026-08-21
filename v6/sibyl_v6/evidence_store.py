from __future__ import annotations

import abc
import datetime as dt
import hashlib
import hmac
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Mapping

OpenUrl = Callable[..., object]


class EvidenceStore(abc.ABC):
    backend: str
    restart_durable: bool

    @abc.abstractmethod
    def put_bytes(self, key: str, payload: bytes) -> None: ...

    @abc.abstractmethod
    def get_bytes(self, key: str) -> bytes: ...

    def contract(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "restart_durable": self.restart_durable,
            "trading_secrets_required": False,
        }


def _safe_key(key: str) -> str:
    clean = key.strip().lstrip("/")
    if not clean or ".." in Path(clean).parts:
        raise ValueError("EVIDENCE_KEY_INVALID")
    return clean


class FileEvidenceStore(EvidenceStore):
    backend = "FILE"
    restart_durable = False

    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / _safe_key(key)

    def put_bytes(self, key: str, payload: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(payload)
        tmp.replace(path)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def contract(self) -> dict[str, object]:
        return {
            **super().contract(),
            "claim": "LOCAL_RUNTIME_EVIDENCE_ONLY",
            "restart_durability_claimed": False,
        }


_GCP_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/token"
)


def gcp_metadata_access_token(timeout: float = 3.0) -> str:
    import json

    request = urllib.request.Request(
        _GCP_METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not token:
        raise RuntimeError("GCP_METADATA_ACCESS_TOKEN_MISSING")
    return str(token)


class GCSEvidenceStore(EvidenceStore):
    backend = "GCS"
    restart_durable = True

    def __init__(
        self,
        bucket: str,
        *,
        token_provider: Callable[[], str] = gcp_metadata_access_token,
        opener: OpenUrl = urllib.request.urlopen,
    ):
        if not bucket:
            raise ValueError("GCS_BUCKET_REQUIRED")
        self.bucket = bucket
        self._token_provider = token_provider
        self._opener = opener

    def put_bytes(self, key: str, payload: bytes) -> None:
        key = _safe_key(key)
        url = (
            "https://storage.googleapis.com/upload/storage/v1/b/"
            + urllib.parse.quote(self.bucket, safe="")
            + "/o?uploadType=media&name="
            + urllib.parse.quote(key, safe="")
        )
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token_provider()}",
                "Content-Type": "application/octet-stream",
            },
        )
        with self._opener(req, timeout=20.0) as response:
            if int(response.status) not in (200, 201):
                raise RuntimeError(f"GCS_UPLOAD_STATUS_{response.status}")

    def get_bytes(self, key: str) -> bytes:
        key = _safe_key(key)
        url = (
            "https://storage.googleapis.com/storage/v1/b/"
            + urllib.parse.quote(self.bucket, safe="")
            + "/o/"
            + urllib.parse.quote(key, safe="")
            + "?alt=media"
        )
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._token_provider()}"}
        )
        with self._opener(req, timeout=20.0) as response:
            return response.read()


class R2EvidenceStore(EvidenceStore):
    """Cloudflare R2 Standard-storage store using only stdlib AWS SigV4.

    Credentials are storage-only runtime secrets. This class has no knowledge of
    wallets, signing keys, trading APIs, or strategy credentials.
    """

    backend = "R2"
    restart_durable = True

    def __init__(
        self,
        account_id: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        *,
        session_token: str | None = None,
        opener: OpenUrl = urllib.request.urlopen,
        now: Callable[[], dt.datetime] | None = None,
    ):
        if not all((account_id, bucket, access_key_id, secret_access_key)):
            raise ValueError("R2_CONFIGURATION_INCOMPLETE")
        self.account_id = account_id
        self.bucket = bucket
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.session_token = session_token
        self._opener = opener
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))

    def _signed_request(self, method: str, key: str, payload: bytes = b"") -> urllib.request.Request:
        key = _safe_key(key)
        now = self._now().astimezone(dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        host = f"{self.account_id}.r2.cloudflarestorage.com"
        canonical_uri = "/" + urllib.parse.quote(self.bucket, safe="-_.~") + "/" + urllib.parse.quote(key, safe="/-_.~")
        payload_hash = hashlib.sha256(payload).hexdigest()
        headers: dict[str, str] = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if self.session_token:
            headers["x-amz-security-token"] = self.session_token
        signed_names = sorted(headers)
        canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in signed_names)
        signed_headers = ";".join(signed_names)
        canonical_request = "\n".join(
            [method, canonical_uri, "", canonical_headers, signed_headers, payload_hash]
        )
        scope = f"{date_stamp}/auto/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )

        def sign(key_bytes: bytes, message: str) -> bytes:
            return hmac.new(key_bytes, message.encode("utf-8"), hashlib.sha256).digest()

        k_date = sign(("AWS4" + self.secret_access_key).encode("utf-8"), date_stamp)
        k_region = sign(k_date, "auto")
        k_service = sign(k_region, "s3")
        k_signing = sign(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self.access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        wire_headers = {
            "Host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": authorization,
        }
        if self.session_token:
            wire_headers["x-amz-security-token"] = self.session_token
        url = f"https://{host}{canonical_uri}"
        return urllib.request.Request(
            url,
            data=payload if method == "PUT" else None,
            method=method,
            headers=wire_headers,
        )

    def put_bytes(self, key: str, payload: bytes) -> None:
        request = self._signed_request("PUT", key, payload)
        with self._opener(request, timeout=20.0) as response:
            if int(response.status) not in (200, 201, 204):
                raise RuntimeError(f"R2_UPLOAD_STATUS_{response.status}")

    def get_bytes(self, key: str) -> bytes:
        request = self._signed_request("GET", key)
        with self._opener(request, timeout=20.0) as response:
            if int(response.status) != 200:
                raise RuntimeError(f"R2_DOWNLOAD_STATUS_{response.status}")
            return response.read()

    def contract(self) -> dict[str, object]:
        return {
            **super().contract(),
            "storage_class": "STANDARD",
            "credential_scope": "STORAGE_ONLY_RUNTIME_SECRET_INJECTION",
        }


def evidence_store_from_env(env: Mapping[str, str] | None = None) -> EvidenceStore:
    values = os.environ if env is None else env
    backend = values.get("SIBYL_V6_EVIDENCE_BACKEND", "FILE").strip().upper() or "FILE"
    if backend == "FILE":
        return FileEvidenceStore(Path(values.get("SIBYL_V6_FILE_STORE_ROOT", "/var/lib/sibyl-v6/store")))
    if backend == "R2":
        return R2EvidenceStore(
            values.get("SIBYL_V6_R2_ACCOUNT_ID", ""),
            values.get("SIBYL_V6_R2_BUCKET", ""),
            values.get("SIBYL_V6_R2_ACCESS_KEY_ID", ""),
            values.get("SIBYL_V6_R2_SECRET_ACCESS_KEY", ""),
            session_token=values.get("SIBYL_V6_R2_SESSION_TOKEN") or None,
        )
    if backend == "GCS":
        return GCSEvidenceStore(values.get("SIBYL_V6_EVIDENCE_BUCKET", ""))
    raise ValueError(f"EVIDENCE_BACKEND_UNSUPPORTED:{backend}")


def persist_directory(store: EvidenceStore, directory: Path, *, source_sha: str) -> list[str]:
    stored: list[str] = []
    prefix = f"evidence/{source_sha}"
    for path in sorted(directory.glob("*.json")):
        key = f"{prefix}/{path.name}"
        store.put_bytes(key, path.read_bytes())
        stored.append(key)
    return stored
