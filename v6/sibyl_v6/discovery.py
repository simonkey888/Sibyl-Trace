from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .feeds import _get_json
from .matcher import MarketDescriptor, PairState, ResolutionRule, compare_markets


@dataclass(frozen=True)
class CandidatePair:
    limitless_slug: str
    polymarket_id: str
    polymarket_slug: str
    limitless_title: str
    polymarket_title: str
    title_similarity: float
    state: str = PairState.CANDIDATE.value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tokens(title: str) -> set[str]:
    stop = {
        "will",
        "the",
        "be",
        "a",
        "an",
        "at",
        "on",
        "by",
        "to",
        "of",
        "in",
        "is",
        "yes",
        "no",
    }
    return {
        x
        for x in re.findall(r"[a-z0-9.$%]+", title.casefold())
        if len(x) > 1 and x not in stop
    }


def _similarity(a: str, b: str) -> float:
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def discover_candidates(
    limit_per_venue: int = 100,
    minimum_similarity: float = 0.35,
) -> list[CandidatePair]:
    _, lmts_payload = _get_json(
        "https://api.limitless.exchange/markets/active?limit=25&page=1"
    )
    lmts = lmts_payload.get("data", []) if isinstance(lmts_payload, dict) else []
    _, poly = _get_json(
        "https://gamma-api.polymarket.com/markets?"
        + urllib.parse.urlencode(
            {"active": "true", "closed": "false", "limit": limit_per_venue}
        )
    )
    if not isinstance(lmts, list) or not isinstance(poly, list):
        raise RuntimeError("PUBLIC_MARKET_DISCOVERY_INVALID")
    out: list[CandidatePair] = []
    for lrow in lmts[:limit_per_venue]:
        if not isinstance(lrow, dict):
            continue
        ltitle = str(lrow.get("title") or lrow.get("question") or "")
        lslug = str(lrow.get("slug") or "")
        if not ltitle or not lslug:
            continue
        for prow in poly[:limit_per_venue]:
            if not isinstance(prow, dict):
                continue
            ptitle = str(prow.get("question") or prow.get("title") or "")
            pid = str(prow.get("id") or prow.get("conditionId") or "")
            pslug = str(prow.get("slug") or "")
            score = _similarity(ltitle, ptitle)
            if ptitle and pid and pslug and score >= minimum_similarity:
                out.append(
                    CandidatePair(
                        lslug,
                        pid,
                        pslug,
                        ltitle,
                        ptitle,
                        round(score, 6),
                    )
                )
    return sorted(
        out,
        key=lambda row: (-row.title_similarity, row.limitless_slug, row.polymarket_id),
    )


def _require_rule_provenance(row: dict[str, Any]) -> None:
    required = (
        "limitless_rule_source_url",
        "polymarket_rule_source_url",
        "limitless_rule_payload_hash",
        "polymarket_rule_payload_hash",
        "verified_at_utc",
    )
    missing = [name for name in required if not str(row.get(name) or "").strip()]
    if missing:
        raise RuntimeError("EXACT_PAIR_PROVENANCE_MISSING:" + ",".join(missing))
    for name in ("limitless_rule_source_url", "polymarket_rule_source_url"):
        parsed = urllib.parse.urlparse(str(row[name]))
        if parsed.scheme != "https" or not parsed.hostname:
            raise RuntimeError(f"EXACT_PAIR_PROVENANCE_URL_INVALID:{name}")
    for name in ("limitless_rule_payload_hash", "polymarket_rule_payload_hash"):
        value = str(row[name]).casefold()
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise RuntimeError(f"EXACT_PAIR_PROVENANCE_HASH_INVALID:{name}")


def load_verified_pairs(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("pairs", []) if isinstance(raw, dict) else []
    accepted: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        _require_rule_provenance(row)
        left_rule = ResolutionRule(**row["limitless_rule"])
        right_rule = ResolutionRule(**row["polymarket_rule"])
        left = MarketDescriptor(
            "LIMITLESS",
            str(row["limitless_slug"]),
            str(row["limitless_title"]),
            left_rule,
            str(row["limitless_rule_payload_hash"]).casefold(),
        )
        right = MarketDescriptor(
            "POLYMARKET",
            str(row["polymarket_id"]),
            str(row["polymarket_title"]),
            right_rule,
            str(row["polymarket_rule_payload_hash"]).casefold(),
        )
        if not row.get("polymarket_slug"):
            raise RuntimeError("EXACT_PAIR_POLYMARKET_SLUG_MISSING")
        comparison = compare_markets(left, right)
        if comparison.state is not PairState.EXACT_EQUIVALENT:
            raise RuntimeError(
                f"configured pair is not exact equivalent: {left.market_id}/{right.market_id}: "
                f"{comparison.state.value} "
                f"{comparison.differing_fields or comparison.unknown_fields}"
            )
        accepted.append({**row, "comparison": comparison.to_dict()})
    return accepted


def build_discovery_evidence(output: Path, verified_pairs_path: Path) -> dict[str, Any]:
    candidates = discover_candidates()
    exact = load_verified_pairs(verified_pairs_path)
    payload = {
        "schema_version": "SIBYL_V6_PAIR_DISCOVERY_V2",
        "observed_at_ms": int(time.time() * 1000),
        "CANDIDATE_PAIR_COUNT": len(candidates),
        "EXACT_EQUIVALENT_PAIR_COUNT": len(exact),
        "candidate_pairs": [row.to_dict() for row in candidates[:50]],
        "exact_pairs": exact,
        "candidate_semantics": "TITLE_SIMILARITY_ONLY_NOT_MATCHED",
        "exact_semantics": "FULL_RULE_EQUIVALENCE_WITH_SOURCE_PAYLOAD_HASHES",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload
