from __future__ import annotations

import json

from .live_recon import _asset, _family, _threshold, _end, fetch_live_catalogs


def _keep(row):
    text = " ".join(str(row.get(k) or "") for k in ("eventTitle", "eventSlug", "title", "question", "description")).casefold()
    return "august 17" in text or "august-17" in text or ("bitcoin" in text and "17-23" in text)


def _row(row):
    return {
        "slug": row.get("slug"),
        "id": row.get("id"),
        "title": row.get("title"),
        "question": row.get("question"),
        "eventTitle": row.get("eventTitle"),
        "eventSlug": row.get("eventSlug"),
        "groupItemTitle": row.get("groupItemTitle"),
        "asset": _asset(row),
        "family": _family(row),
        "threshold": _threshold(row),
        "end": _end(row).isoformat() if _end(row) else None,
        "startAt": row.get("startAt"),
        "startDate": row.get("startDate"),
        "expirationTimestamp": row.get("expirationTimestamp"),
        "endDate": row.get("endDate"),
        "description": str(row.get("description") or "")[:1200],
        "keys": sorted(row.keys()),
    }


if __name__ == "__main__":
    l, p = fetch_live_catalogs()
    print(json.dumps({
        "limitless": [_row(x) for x in l if _keep(x)],
        "polymarket": [_row(x) for x in p if _keep(x)],
    }, indent=2, sort_keys=True, default=str))
