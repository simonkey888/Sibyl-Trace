from __future__ import annotations

from typing import Any

from . import rule_audit
from .catalogs_v2 import fetch_live_catalogs
from .rule_audit_runner_v2 import equivalent_event_key


def audit_current_pairs() -> dict[str, Any]:
    """Run the authoritative matcher against the current public venue catalogs.

    The rule-audit module is kept deterministic/testable by dependency injection.
    Runtime selection uses the same catalog/event-key implementation as the
    exact-head authoritative audit workflow and restores module globals after
    the call.
    """
    original_fetch = rule_audit.fetch_live_catalogs
    original_key = rule_audit._event_key
    try:
        rule_audit.fetch_live_catalogs = fetch_live_catalogs
        rule_audit._event_key = equivalent_event_key
        return rule_audit.audit_live_pairs()
    finally:
        rule_audit.fetch_live_catalogs = original_fetch
        rule_audit._event_key = original_key


def select_current_exact_pair(
    audit: dict[str, Any], preferred: set[tuple[str, str]] | None = None
) -> dict[str, Any] | None:
    exact = [row for row in (audit.get("exact_pairs") or []) if isinstance(row, dict)]
    exact.sort(key=lambda row: (str(row.get("limitless_slug") or ""), str(row.get("polymarket_slug") or "")))
    if preferred:
        for row in exact:
            key = (str(row.get("limitless_slug") or ""), str(row.get("polymarket_slug") or ""))
            if key in preferred:
                return row
    return exact[0] if exact else None
