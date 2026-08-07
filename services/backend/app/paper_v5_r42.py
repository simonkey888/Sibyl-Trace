from __future__ import annotations

import argparse
import copy
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import paper_v5_r4 as r4
from app.models import Wallet
from app.models_v5 import PaperV5Execution, PaperV5ExecutionEvidence, PaperV5Prediction

COHORT_ID = "PAPER_V5_R4_2_AUDIT_CORRECTIONS_2026_08_07"
EXECUTION_MODEL = "L2_TAKER_FAK_ARRIVAL_BOOK_V4_POST_DELAY_REVALIDATION_SHADOW_IMPACT"

_apply_r41_report = r4._apply_r4_report
_write_ledger_r41 = r4._write_ledger_r4


def _price_key(value: Any) -> str:
    return format(Decimal(str(value)).normalize(), "f")


class _R42BookUnavailable(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.response = SimpleNamespace(status_code=404)


class _R42TruthClient:
    """Read-only CLOB/Gamma proxy with conservative run-local PAPER self-impact."""

    def __init__(self, inner: Any):
        self._inner = inner
        self._condition_id = ""
        self._market_slug = ""
        self._book_calls = 0
        self._last_market: dict[str, Any] | None = None
        self._last_revalidated_market: dict[str, Any] | None = None
        self.decision_book: dict[str, Any] | None = None
        self.arrival_book: dict[str, Any] | None = None
        self._shadow: dict[tuple[str, str, str], float] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def start_signal(self, condition_id: str, market_slug: str) -> None:
        self._condition_id = condition_id
        self._market_slug = market_slug
        self._book_calls = 0
        self._last_market = None
        self._last_revalidated_market = None
        self.decision_book = None
        self.arrival_book = None

    def finish_signal(self) -> None:
        self._condition_id = ""
        self._market_slug = ""
        self._book_calls = 0
        self._last_market = None
        self._last_revalidated_market = None
        self.decision_book = None
        self.arrival_book = None

    @property
    def last_revalidated_market(self) -> dict[str, Any] | None:
        return self._last_revalidated_market

    def _get(self, url: str, params: Any = None) -> Any:
        data = self._inner._get(url, params=params)
        if self._condition_id and "/markets/slug/" in str(url) and isinstance(data, dict):
            self._last_market = dict(data)
        return data

    def _shadow_for_asset(self, asset_id: str) -> list[tuple[str, str, float]]:
        rows: list[tuple[str, str, float]] = []
        for (asset, side, price), amount in sorted(self._shadow.items()):
            if asset == asset_id and amount > 0:
                rows.append((side, price, amount))
        return rows

    def _apply_shadow(self, asset_id: str, raw_book: dict[str, Any]) -> dict[str, Any]:
        book = copy.deepcopy(raw_book)
        adjusted = False
        for side, field in (("BUY", "asks"), ("SELL", "bids")):
            source = book.get(field)
            if not isinstance(source, list):
                continue
            rewritten: list[dict[str, Any]] = []
            for item in source:
                if not isinstance(item, dict):
                    continue
                row = dict(item)
                try:
                    key = _price_key(row.get("price"))
                except Exception:
                    rewritten.append(row)
                    continue
                debt = self._shadow.get((asset_id, side, key), 0.0)
                try:
                    size = float(row.get("size") or 0)
                except (TypeError, ValueError):
                    size = 0.0
                remaining = max(size - debt, 0.0)
                if debt > 0:
                    adjusted = True
                if remaining > 1e-12:
                    row["size"] = format(Decimal(str(remaining)).normalize(), "f")
                    rewritten.append(row)
            book[field] = rewritten
        if adjusted:
            source_hash = str(raw_book.get("hash") or "") or None
            book["source_hash"] = source_hash
            book["hash"] = "shadow-" + r4._canonical_hash(
                {
                    "source_hash": source_hash,
                    "asset_id": asset_id,
                    "shadow": self._shadow_for_asset(asset_id),
                    "asks": book.get("asks") or [],
                    "bids": book.get("bids") or [],
                }
            )
        return book

    def order_book(self, asset_id: str) -> dict[str, Any]:
        self._book_calls += 1
        is_arrival = self._condition_id and self._book_calls >= 2
        if is_arrival:
            delay = float((self._last_market or {}).get("secondsDelay") or 0)
            if delay > 0:
                latest = r4._market_by_condition(
                    self._inner,
                    self._condition_id,
                    self._market_slug,
                )
                self._last_revalidated_market = dict(latest)
                self._last_market = dict(latest)
                if not r4._is_trade_ready(latest):
                    raise _R42BookUnavailable("post_delay_market_not_trade_ready")
        raw = self._inner.order_book(asset_id)
        book = self._apply_shadow(asset_id, raw)
        if self._condition_id:
            if self._book_calls == 1:
                self.decision_book = copy.deepcopy(book)
            else:
                self.arrival_book = copy.deepcopy(book)
        return book

    def record_fill(self, asset_id: str, side: str, execution: PaperV5Execution) -> None:
        if execution.status not in {"FILLED", "PARTIAL_FILLED"}:
            return
        remaining = max(float(execution.filled_shares or 0), 0.0)
        if remaining <= 0 or not isinstance(self.arrival_book, dict):
            return
        field = "asks" if side == "BUY" else "bids"
        levels: list[tuple[float, float, str]] = []
        for item in self.arrival_book.get(field) or []:
            if not isinstance(item, dict):
                continue
            try:
                price = float(item.get("price") or 0)
                size = float(item.get("size") or 0)
            except (TypeError, ValueError):
                continue
            if 0 < price < 1 and size > 0:
                levels.append((price, size, _price_key(price)))
        levels.sort(key=lambda row: row[0], reverse=side == "SELL")
        limit = float(execution.worst_price_limit or (1.0 if side == "BUY" else 0.0))
        for price, available, key in levels:
            if remaining <= 1e-12:
                break
            if side == "BUY" and price > limit:
                break
            if side == "SELL" and price < limit:
                break
            take = min(available, remaining)
            if take <= 0:
                continue
            shadow_key = (asset_id, side, key)
            self._shadow[shadow_key] = self._shadow.get(shadow_key, 0.0) + take
            remaining -= take
        if remaining > 1e-6:
            raise RuntimeError("shadow self-impact could not reconcile filled shares to arrival depth")


class PaperEngineV5R42(r4.PaperEngineV5R4):
    def __init__(self, settings: Any, client: Any):
        self._truth_client = _R42TruthClient(client)
        super().__init__(settings, self._truth_client)

    def _refresh_revalidated_evidence(self, db: Session, prediction: PaperV5Prediction) -> None:
        latest = self._truth_client.last_revalidated_market
        if latest is None:
            return
        evidence = db.get(PaperV5ExecutionEvidence, prediction.id)
        if evidence is None:
            return
        metadata_hash, evidence_hash, state = r4._evidence_payload(
            prediction,
            latest,
            decision_book=self._truth_client.decision_book,
            arrival_book=self._truth_client.arrival_book,
            decision_received_at_ms=evidence.decision_received_at_ms,
            arrival_received_at_ms=evidence.arrival_received_at_ms,
            fee_rate_bps_crosscheck=evidence.fee_rate_bps_crosscheck,
        )
        evidence.market_metadata_hash = metadata_hash
        evidence.execution_evidence_hash = evidence_hash
        evidence.market_active = state.get("active")
        evidence.market_closed = state.get("closed")
        evidence.accepting_orders = state.get("acceptingOrders")
        evidence.enable_order_book = state.get("enableOrderBook")
        evidence.official_seconds_delay = float(state.get("secondsDelay") or 0)
        db.add(evidence)
        db.commit()

    def process(self, db: Session, wallet: Wallet, activity: dict[str, Any]) -> bool:
        condition_id = str(activity.get("conditionId") or "")
        market_slug = str(activity.get("slug") or "").strip()
        source_key, _ = r4.legacy._source_identity(wallet.address, activity)
        self._truth_client.start_signal(condition_id, market_slug)
        try:
            handled = super().process(db, wallet, activity)
            if not handled:
                return handled
            prediction = db.scalar(
                select(PaperV5Prediction).where(PaperV5Prediction.source_key == source_key)
            )
            if prediction is None:
                return handled
            execution = db.scalar(
                select(PaperV5Execution).where(
                    PaperV5Execution.prediction_id == prediction.id
                )
            )
            self._refresh_revalidated_evidence(db, prediction)
            if execution is not None:
                self._truth_client.record_fill(prediction.asset_id, prediction.side, execution)
            return handled
        finally:
            self._truth_client.finish_signal()


def _directional_decay(side: str, later: float | None, source: float) -> float | None:
    if later is None:
        return None
    return later - source if side == "BUY" else source - later


def _write_ledger_r42(original_writer: Any, db: Session, path: Path) -> None:
    _write_ledger_r41(original_writer, db, path)
    rewritten: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(raw)
        execution = row.get("execution") or {}
        evidence = row.get("execution_evidence") or {}
        source = float(row.get("source_price") or 0)
        side = str(row.get("side") or "").upper()
        decision = execution.get("decision_best_price")
        average = execution.get("average_fill_price")
        effective = execution.get("effective_price")
        filled = float(execution.get("filled_shares") or 0)
        fee = float(execution.get("fee_usd") or 0)
        row["copy_decay"] = {
            "direction": "positive_is_worse_than_source",
            "decision_vs_source": _directional_decay(
                side, float(decision) if decision is not None else None, source
            ),
            "raw_fill_vs_source": _directional_decay(
                side, float(average) if average is not None else None, source
            ),
            "effective_fill_vs_source": _directional_decay(
                side, float(effective) if effective is not None else None, source
            ),
            "fee_per_filled_share": fee / filled if filled > 0 else None,
        }
        row["fee_provenance"] = {
            "primary": "CLOB getClobMarketInfo fd",
            "fee_rate": execution.get("fee_rate"),
            "fee_exponent": execution.get("fee_exponent"),
            "fee_rate_bps_crosscheck": evidence.get("fee_rate_bps_crosscheck"),
        }
        row["shadow_self_impact_applied"] = any(
            str(execution.get(key) or "").startswith("shadow-")
            for key in ("decision_book_hash", "arrival_book_hash")
        )
        rewritten.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
    path.write_text("\n".join(rewritten) + ("\n" if rewritten else ""), encoding="utf-8")


def _apply_r42_report(
    report: dict[str, Any], db: Session, baseline: dict[str, int]
) -> dict[str, Any]:
    report = _apply_r41_report(report, db, baseline)
    report["methodology"].update(
        {
            "execution_model": EXECUTION_MODEL,
            "post_delay_market_state_revalidation": True,
            "post_delay_market_state_revalidation_scope": (
                "markets with Gamma secondsDelay > 0; revalidated immediately before arrival-book fetch"
            ),
            "shadow_self_impact": True,
            "shadow_self_impact_scope": (
                "run-local conservative no-replenishment depletion by asset/side/price"
            ),
            "shadow_self_impact_live_claim": False,
            "copy_decay_metrics_in_ledger": True,
            "fee_provenance_in_ledger": True,
            "simulated_latency_field_semantics": (
                "always zero in R4.2; official exchange delay is stored separately"
            ),
        }
    )
    return report


def run(output_dir: Path) -> int:
    original_cohort = r4.COHORT_ID
    original_model = r4.EXECUTION_MODEL
    original_engine = r4.PaperEngineV5R4
    original_apply = r4._apply_r4_report
    original_writer = r4._write_ledger_r4
    r4.COHORT_ID = COHORT_ID
    r4.EXECUTION_MODEL = EXECUTION_MODEL
    r4.PaperEngineV5R4 = PaperEngineV5R42
    r4._apply_r4_report = _apply_r42_report
    r4._write_ledger_r4 = _write_ledger_r42
    try:
        return r4.run(output_dir)
    finally:
        r4.COHORT_ID = original_cohort
        r4.EXECUTION_MODEL = original_model
        r4.PaperEngineV5R4 = original_engine
        r4._apply_r4_report = original_apply
        r4._write_ledger_r4 = original_writer


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Sibyl Trace PAPER V5 R4.2 audit-corrected")
    parser.add_argument("--output-dir", type=Path, default=Path("paper-v5-output"))
    args = parser.parse_args()
    raise SystemExit(run(args.output_dir))


if __name__ == "__main__":
    main()
