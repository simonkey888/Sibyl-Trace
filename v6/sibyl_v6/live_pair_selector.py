from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import rule_audit
from .catalogs_v2 import fetch_live_catalogs
from .rule_audit_runner_v2 import equivalent_event_key


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _persist_runtime_audit(payload: dict[str, Any]) -> None:
    target = os.environ.get("SIBYL_V6_AUTHORITATIVE_AUDIT_EVIDENCE")
    if not target:
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def audit_current_pairs() -> dict[str, Any]:
    """Run the authoritative matcher against current public venue catalogs.

    Runtime selection uses exactly the object returned here. When the evidence
    path is configured, the same object is atomically persisted before it is
    returned to the selector; CI can therefore prove selected-pair membership
    in the authoritative audit cycle that actually drove the run.
    """
    original_fetch = rule_audit.fetch_live_catalogs
    original_key = rule_audit._event_key
    try:
        rule_audit.fetch_live_catalogs = fetch_live_catalogs
        rule_audit._event_key = equivalent_event_key
        raw = rule_audit.audit_live_pairs()
        audit = dict(raw)
        audit["authoritative_audit_hash"] = _canonical_hash(raw)
        _persist_runtime_audit(audit)
        return audit
    finally:
        rule_audit.fetch_live_catalogs = original_fetch
        rule_audit._event_key = original_key


def select_current_exact_pair(
    audit: dict[str, Any], preferred: set[tuple[str, str]] | None = None
) -> dict[str, Any] | None:
    exact = [row for row in (audit.get("exact_pairs") or []) if isinstance(row, dict)]
    exact.sort(
        key=lambda row: (
            str(row.get("limitless_slug") or ""),
            str(row.get("polymarket_slug") or ""),
        )
    )
    if preferred:
        for row in exact:
            key = (
                str(row.get("limitless_slug") or ""),
                str(row.get("polymarket_slug") or ""),
            )
            if key in preferred:
                return row
    return exact[0] if exact else None
