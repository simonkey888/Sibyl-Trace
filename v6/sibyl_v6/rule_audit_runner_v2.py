from __future__ import annotations

import json

from . import rule_audit
from .catalogs_v2 import fetch_live_catalogs


if __name__ == "__main__":
    rule_audit.fetch_live_catalogs = fetch_live_catalogs
    print(json.dumps(rule_audit.audit_live_pairs(), indent=2, sort_keys=True, default=str))
