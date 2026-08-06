from __future__ import annotations

import base64
import hashlib
import io
import shutil
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "bootstrap"
EXPECTED_SHA256 = "15f9aff1b783c850838b8edef027c2c9a1c639374656b75a06190dac2a55fe35"

encoded = "".join(
    path.read_text(encoding="ascii")
    for path in sorted((BOOTSTRAP / "chunks").glob("part-*"))
)
archive = base64.b64decode(encoded, validate=True)
actual_sha256 = hashlib.sha256(archive).hexdigest()
if actual_sha256 != EXPECTED_SHA256:
    raise SystemExit(
        f"bootstrap archive checksum mismatch: {actual_sha256} != {EXPECTED_SHA256}"
    )

with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
    for member in bundle.getmembers():
        target = (ROOT / member.name).resolve()
        if target != ROOT and ROOT not in target.parents:
            raise SystemExit(f"unsafe archive member: {member.name}")
    bundle.extractall(ROOT, filter="data")

shutil.rmtree(BOOTSTRAP)
workflow = ROOT / ".github" / "workflows" / "bootstrap.yml"
workflow.unlink(missing_ok=True)
print(f"Materialized Sibyl Trace from verified archive {actual_sha256}")
