from __future__ import annotations

import json
import re
from typing import Any

from . import rule_audit
from .catalogs_v2 import fetch_live_catalogs


def equivalent_event_key(row: dict[str, Any]) -> str:
    text = str(row.get("eventTitle") or row.get("eventSlug") or "").casefold()
    # Venue labels may append the calendar year even when the other venue omits it.
    # The year remains independently proven by the rule/window parser; it is only
    # removed from candidate grouping here.
    tokens = [x for x in re.findall(r"[a-z0-9]+", text) if re.fullmatch(r"20\d{2}", x) is None]
    return " ".join(tokens)


if __name__ == "__main__":
    rule_audit.fetch_live_catalogs = fetch_live_catalogs
    rule_audit._event_key = equivalent_event_key
    print(json.dumps(rule_audit.audit_live_pairs(), indent=2, sort_keys=True, default=str))
