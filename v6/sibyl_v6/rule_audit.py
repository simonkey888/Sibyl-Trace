from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .feeds import _get_json
from .live_recon import _asset, _family, _threshold, fetch_live_catalogs
from .matcher import MarketDescriptor, PairState, ResolutionRule, compare_markets

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _clean(text: Any) -> str:
    raw = html.unescape(str(text or ""))
    raw = re.sub(r"<[^>]+>", " ", raw)
    return " ".join(raw.replace("“", '"').replace("”", '"').replace("’", "'").split())


def _event_key(row: dict[str, Any]) -> str:
    text = str(row.get("eventTitle") or row.get("eventSlug") or "")
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _year(row: dict[str, Any]) -> int | None:
    for key in ("startAt", "startDate", "createdAt", "endDate", "expirationDate"):
        value = row.get(key)
        if not value:
            continue
        text = str(value)
        match = re.search(r"\b(20\d{2})\b", text)
        if match:
            return int(match.group(1))
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).year
        except ValueError:
            continue
    return None


def _weekly_window(event_title: str, year: int) -> tuple[str, str] | None:
    # Handles names such as "What price will Bitcoin hit August 17-23?".
    match = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\s*[-–]\s*(\d{1,2})\b",
        event_title,
        re.I,
    )
    if not match:
        return None
    month = datetime.strptime(match.group(1), "%B").month
    first = int(match.group(2))
    last = int(match.group(3))
    start = datetime(year, month, first, 0, 0, tzinfo=ET).astimezone(UTC)
    # The rule explicitly includes the 11:59 PM ET 1-minute candle. Canonicalize
    # window_end to that candle's timestamp, not to the venue's trading-close instant.
    end = datetime(year, month, last, 23, 59, tzinfo=ET).astimezone(UTC)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def _rule_from_market(row: dict[str, Any]) -> tuple[ResolutionRule | None, list[str]]:
    description = _clean(row.get("description"))
    title = str(row.get("title") or row.get("question") or "")
    event_title = str(row.get("eventTitle") or "")
    asset = _asset(row)
    family = _family(row)
    threshold = _threshold(row)
    year = _year(row)
    unknown: list[str] = []

    if asset != "BTC" or "BTC/USDT" not in description:
        unknown.append("underlying")
        underlying = None
    else:
        underlying = "BTC/USDT"

    if family == "ABOVE" and re.search(r"equal to or greater than|equal to or higher than", description, re.I):
        polarity = "YES_IF_THRESHOLD_REACHED_ELSE_NO"
        operator = ">="
        field = "FINAL_HIGH"
    elif family == "BELOW" and re.search(r"equal to or lower than|equal to or less than", description, re.I):
        polarity = "YES_IF_THRESHOLD_REACHED_ELSE_NO"
        operator = "<="
        field = "FINAL_LOW"
    else:
        unknown.extend(["polarity", "comparison_operator"])
        polarity = None
        operator = None
        field = None

    if threshold is None:
        unknown.append("threshold")
        threshold_text = None
    else:
        threshold_text = str(threshold)

    binance = "binance.com/en/trade/BTC_USDT" in description
    one_minute = bool(re.search(r"\b1[- ]?minute\b|\b1m\b", description, re.I))
    if not (binance and one_minute and field):
        unknown.append("reference_source")
        reference_source = None
    else:
        reference_source = f"BINANCE:BTC/USDT:1M:{field}"

    window = _weekly_window(event_title, year) if year else None
    explicit_weekly_window = bool(
        re.search(r"12:00\s*AM\s*ET.*first date.*11:59\s*PM\s*ET.*last", description, re.I)
    )
    if not window or not explicit_weekly_window:
        unknown.extend(["window_start_utc", "window_end_utc"])
        window_start = window_end = None
    else:
        window_start, window_end = window

    immediate = bool(re.search(r"immediately resolve to [\"']?Yes", description, re.I))
    otherwise_no = bool(re.search(r"otherwise.*resolve to [\"']?No", description, re.I))
    if immediate and otherwise_no and window_end:
        resolution_instant = f"IMMEDIATE_AFTER_QUALIFYING_1M_CANDLE_ELSE_AFTER_{window_end}"
    else:
        unknown.append("resolution_instant_utc")
        resolution_instant = None

    if threshold_text and field and operator:
        price_to_beat = f"FIXED_TITLE_THRESHOLD_{threshold_text}_AGAINST_{field}"
    else:
        unknown.append("price_to_beat_construction")
        price_to_beat = None

    if operator in (">=", "<="):
        tie = "EQUALITY_COUNTS_AS_YES"
    else:
        unknown.append("equality_tie_handling")
        tie = None

    # These are known market-level facts, not guessed behavior: the complete
    # authoritative market rule text contains no market-specific invalid,
    # cancellation, or alternate-oracle clause. Platform adjudication mechanics
    # are recorded separately; absence of a market-specific clause is explicit.
    lower = description.casefold()
    invalid_markers = ("invalid market", "market is invalid", "invalidated")
    cancel_markers = ("cancelled", "canceled", "cancellation")
    fallback_markers = ("if binance", "if the resolution source", "alternate source", "alternative source", "fallback")
    invalid_rule = "NO_MARKET_SPECIFIC_INVALID_RULE" if not any(x in lower for x in invalid_markers) else None
    cancellation_rule = "NO_MARKET_SPECIFIC_CANCELLATION_RULE" if not any(x in lower for x in cancel_markers) else None
    fallback_rule = "NO_MARKET_SPECIFIC_ORACLE_FALLBACK" if not any(x in lower for x in fallback_markers) else None
    if invalid_rule is None:
        unknown.append("invalid_market_rules")
    if cancellation_rule is None:
        unknown.append("cancellation_rules")
    if fallback_rule is None:
        unknown.append("fallback_oracle_failure_rules")

    # Both venues expose binary YES/NO claims whose winning side redeems for $1
    # and losing side for $0; dispute/adjudication mechanisms are intentionally
    # separate from the event predicate equivalence fingerprint.
    outcomes = str(row.get("outcomes") or row.get("tokens") or "").casefold()
    binary_shape = ("yes" in outcomes and "no" in outcomes) or family in ("ABOVE", "BELOW")
    if binary_shape:
        settlement = "BINARY_YES_NO_WINNER_REDEEMS_1_LOSER_0"
    else:
        unknown.append("settlement_semantics")
        settlement = None

    return ResolutionRule(
        underlying=underlying,
        polarity=polarity,
        threshold=threshold_text,
        comparison_operator=operator,
        reference_source=reference_source,
        window_start_utc=window_start,
        window_end_utc=window_end,
        resolution_instant_utc=resolution_instant,
        price_to_beat_construction=price_to_beat,
        equality_tie_handling=tie,
        invalid_market_rules=invalid_rule,
        cancellation_rules=cancellation_rule,
        fallback_oracle_failure_rules=fallback_rule,
        settlement_semantics=settlement,
    ), sorted(set(unknown))


def _detail(venue: str, slug: str) -> tuple[str, dict[str, Any]]:
    if venue == "LIMITLESS":
        url = "https://api.limitless.exchange/markets/" + urllib.parse.quote(slug, safe="")
    else:
        url = "https://gamma-api.polymarket.com/markets/slug/" + urllib.parse.quote(slug, safe="")
    _, payload = _get_json(url)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{venue}_DETAIL_INVALID:{slug}")
    return url, payload


def audit_live_pairs() -> dict[str, Any]:
    limitless, polymarket = fetch_live_catalogs()
    poly_by_event: dict[str, list[dict[str, Any]]] = {}
    for row in polymarket:
        poly_by_event.setdefault(_event_key(row), []).append(row)

    candidates: list[dict[str, Any]] = []
    exact: list[dict[str, Any]] = []
    for left in limitless:
        event_key = _event_key(left)
        if not event_key or event_key not in poly_by_event:
            continue
        lf, lt = _family(left), _threshold(left)
        if lf not in ("ABOVE", "BELOW") or lt is None:
            continue
        for right in poly_by_event[event_key]:
            if _asset(left) != _asset(right) or _family(right) != lf or _threshold(right) != lt:
                continue
            lslug, pslug = str(left.get("slug") or ""), str(right.get("slug") or "")
            if not lslug or not pslug:
                continue
            candidates.append({"limitless_slug": lslug, "polymarket_slug": pslug, "event": left.get("eventTitle"), "threshold": lt, "family": lf})
            try:
                lurl, lpayload = _detail("LIMITLESS", lslug)
                purl, ppayload = _detail("POLYMARKET", pslug)
            except Exception as exc:
                candidates[-1]["audit_error"] = str(exc)
                continue
            # Preserve event metadata used by the rule text if detail endpoints omit it.
            lfull = {**left, **lpayload}
            pfull = {**right, **ppayload}
            lrule, lunknown = _rule_from_market(lfull)
            prule, punknown = _rule_from_market(pfull)
            comparison = compare_markets(
                MarketDescriptor("LIMITLESS", lslug, str(lfull.get("title") or ""), lrule, _sha(lpayload)),
                MarketDescriptor("POLYMARKET", pslug, str(pfull.get("question") or ""), prule, _sha(ppayload)),
            )
            audited = {
                **candidates[-1],
                "limitless_rule_source_url": lurl,
                "polymarket_rule_source_url": purl,
                "limitless_rule_payload_hash": _sha(lpayload),
                "polymarket_rule_payload_hash": _sha(ppayload),
                "limitless_rule": lrule.canonical() if lrule else None,
                "polymarket_rule": prule.canonical() if prule else None,
                "limitless_unknown_fields": lunknown,
                "polymarket_unknown_fields": punknown,
                "comparison": comparison.to_dict(),
                "limitless_description": _clean(lfull.get("description")),
                "polymarket_description": _clean(pfull.get("description")),
                "limitless_title": str(lfull.get("title") or ""),
                "polymarket_title": str(pfull.get("question") or pfull.get("title") or ""),
                "polymarket_id": str(pfull.get("id") or pfull.get("conditionId") or ""),
            }
            candidates[-1] = audited
            if not lunknown and not punknown and comparison.state is PairState.EXACT_EQUIVALENT:
                exact.append(audited)

    return {
        "schema_version": "SIBYL_V6_AUTHORITATIVE_RULE_AUDIT_V1",
        "catalog_counts": {"limitless": len(limitless), "polymarket": len(polymarket)},
        "CANDIDATE_PAIR_COUNT": len(candidates),
        "EXACT_EQUIVALENT_PAIR_COUNT": len(exact),
        "candidates": candidates,
        "exact_pairs": exact,
        "rule_scope": "EVENT_PREDICATE_AND_PAYOUT_SEMANTICS;_PLATFORM_DISPUTE_MECHANISM_RECORDED_SEPARATELY",
        "platform_adjudication": {
            "LIMITLESS": "venue resolution then on-chain payout settlement; manual review possible",
            "POLYMARKET": "UMA optimistic-oracle proposal/dispute/DVM path",
        },
    }


if __name__ == "__main__":
    print(json.dumps(audit_live_pairs(), indent=2, sort_keys=True, default=str))
