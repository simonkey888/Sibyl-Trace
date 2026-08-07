from pathlib import Path

p = Path("services/backend/app/paper_v5_r4.py")
text = p.read_text()
text = text.replace("from pathlib import Path\n", "from pathlib import Path\nfrom urllib.parse import quote\n")
text = text.replace(
    'COHORT_ID = "PAPER_V5_R4_AUDIT_RECONCILIATION_2026_08_07"\nEXECUTION_MODEL = "L2_TAKER_FAK_ARRIVAL_BOOK_V2_AUDIT_RECONCILED"',
    'COHORT_ID = "PAPER_V5_R4_1_REPORT_PUBLISH_TRUTH_2026_08_07"\nEXECUTION_MODEL = "L2_TAKER_FAK_ARRIVAL_BOOK_V3_SLUG_IDENTITY"',
)
old = '''def _market_by_condition(client: Any, condition_id: str) -> dict[str, Any]:\n    data = client._get(\n        f"{client.settings.gamma_api_base}/markets",\n        {"condition_ids": [condition_id], "limit": 10},\n    )\n    rows = (\n        data\n        if isinstance(data, list)\n        else (data.get("markets") or [] if isinstance(data, dict) else [])\n    )\n    for market in rows:\n        if not isinstance(market, dict):\n            continue\n        current = str(market.get("conditionId") or market.get("condition_id") or "")\n        if current == condition_id:\n            return market\n    raise PolymarketError("Gamma market details did not match requested condition")\n'''
new = '''def _market_by_condition(\n    client: Any, condition_id: str, market_slug: str\n) -> dict[str, Any]:\n    slug = str(market_slug or "").strip()\n    if not slug:\n        raise PolymarketError("market_slug_unavailable")\n    data = client._get(\n        f"{client.settings.gamma_api_base}/markets/slug/{quote(slug, safe='-')}"\n    )\n    if not isinstance(data, dict):\n        raise PolymarketError("Gamma market slug response was not an object")\n    current = str(data.get("conditionId") or data.get("condition_id") or "")\n    if current != condition_id:\n        raise PolymarketError("Gamma slug market conditionId mismatch")\n    return data\n'''
if old not in text:
    raise SystemExit("market lookup anchor missing")
text = text.replace(old, new)

# Ensure official exchange delay is never written into the legacy synthetic-latency field.
old = '''class PaperEngineV5R4(legacy.PaperEngineV5):\n    def _no_fill(\n'''
new = '''class PaperEngineV5R4(legacy.PaperEngineV5):\n    @staticmethod\n    def _execution_kwargs_without_synthetic_latency(kwargs: dict[str, Any]) -> dict[str, Any]:\n        cleaned = dict(kwargs)\n        rules = cleaned.get("rules")\n        if rules is not None:\n            cleaned["rules"] = replace(rules, order_delay_ms=0)\n        return cleaned\n\n    def _reject(\n        self,\n        db: Session,\n        prediction: PaperV5Prediction,\n        reason: str,\n        **kwargs: Any,\n    ) -> None:\n        super()._reject(\n            db,\n            prediction,\n            reason,\n            **self._execution_kwargs_without_synthetic_latency(kwargs),\n        )\n\n    def _no_fill(\n'''
if old not in text:
    raise SystemExit("engine class anchor missing")
text = text.replace(old, new)
text = text.replace(
    'db.add(legacy._execution_row(prediction, status="NO_FILL", reason=reason, **kwargs))',
    'db.add(\n            legacy._execution_row(\n                prediction,\n                status="NO_FILL",\n                reason=reason,\n                **self._execution_kwargs_without_synthetic_latency(kwargs),\n            )\n        )',
)
text = text.replace(
    'condition_id = str(activity.get("conditionId") or "")\n',
    'condition_id = str(activity.get("conditionId") or "")\n        market_slug = str(activity.get("slug") or "").strip()\n',
)
text = text.replace(
    "_market_by_condition(self.client, condition_id)",
    "_market_by_condition(self.client, condition_id, market_slug)",
)

old = '''            "execution_model": EXECUTION_MODEL,\n            "official_seconds_delay_source": "Gamma market.secondsDelay",\n            "synthetic_canonical_latency": False,\n            "actual_request_gap_recorded": True,\n            "market_state_404_classification": True,\n            "active_tradable_404_is_data_failure": True,\n            "fee_schedule_dynamic": True,\n            "fee_schedule_source": "CLOB getClobMarketInfo fd",\n            "fee_rate_bps_crosscheck": True,\n            "execution_evidence_hash": True,\n            "summary_ledger_reconciliation": True,\n            "legacy_history_rewritten": False,\n'''
new = '''            "execution_model": EXECUTION_MODEL,\n            "official_seconds_delay_source": "Gamma market.secondsDelay",\n            "delayed_market_arrival_delay_basis": (\n                "Gamma market.secondsDelay; per-market exchange-declared seconds"\n            ),\n            "delayed_market_arrival_delay_ms": None,\n            "regular_arrival_delay_basis": (\n                "immediate public-book refetch; actual_gap_ms measured"\n            ),\n            "regular_arrival_delay_ms": None,\n            "synthetic_canonical_latency": False,\n            "simulated_latency_field_semantics": (\n                "always zero in R4.1; official exchange delay is stored separately"\n            ),\n            "actual_request_gap_recorded": True,\n            "immediate_post_fill_marking": True,\n            "end_cycle_mark_refresh": True,\n            "unknown_official_delay_fail_closed": True,\n            "market_identity_source": (\n                "Data API activity.slug -> Gamma /markets/slug/{slug} + exact conditionId"\n            ),\n            "market_identity_exact": True,\n            "market_state_404_classification": True,\n            "active_tradable_404_is_data_failure": True,\n            "fee_schedule_dynamic": True,\n            "fee_schedule_source": "CLOB getClobMarketInfo fd",\n            "fee_rate_bps_crosscheck": True,\n            "execution_evidence_hash": True,\n            "summary_ledger_reconciliation": True,\n            "legacy_history_rewritten": False,\n'''
if old not in text:
    raise SystemExit("report methodology anchor missing")
text = text.replace(old, new)
text = text.replace(
    'description="Run Sibyl Trace PAPER V5 R4 audit-reconciled"',
    'description="Run Sibyl Trace PAPER V5 R4.1 truth-closed"',
)
p.write_text(text)

# New clean state tag and exact R4.1 contract.
p = Path(".github/workflows/github-paper-v5.yml")
text = p.read_text()
text = text.replace("RELEASE_TAG: github-paper-v5-state-v4", "RELEASE_TAG: github-paper-v5-state-v4-1")
text = text.replace(
    "PAPER_V5_R4_AUDIT_RECONCILIATION_2026_08_07",
    "PAPER_V5_R4_1_REPORT_PUBLISH_TRUTH_2026_08_07",
)
text = text.replace("No R4 V5 state exists yet", "No R4.1 V5 state exists yet")
text = text.replace("clean R4 cohort", "clean R4.1 cohort")
text = text.replace("Verified R4 V5 state restored", "Verified R4.1 V5 state restored")
text = text.replace("rolling PAPER V5 R4 state", "rolling PAPER V5 R4.1 state")
p.write_text(text)

p = Path("services/backend/app/cloudflare_snapshot.py")
text = p.read_text()
text = text.replace(
    'v5.get("cohort_id") != "PAPER_V5_R4_AUDIT_RECONCILIATION_2026_08_07"',
    'v5.get("cohort_id") != "PAPER_V5_R4_1_REPORT_PUBLISH_TRUTH_2026_08_07"',
)
text = text.replace(
    'method.get("execution_model") != "L2_TAKER_FAK_ARRIVAL_BOOK_V2_AUDIT_RECONCILED"',
    'method.get("execution_model") != "L2_TAKER_FAK_ARRIVAL_BOOK_V3_SLUG_IDENTITY"',
)
needle = '''        or method.get("summary_ledger_reconciliation") is not True\n    ):\n        raise ValueError("PAPER V5 snapshot violates truthful-execution methodology")\n'''
replacement = '''        or method.get("summary_ledger_reconciliation") is not True\n        or method.get("unknown_official_delay_fail_closed") is not True\n        or method.get("market_identity_exact") is not True\n        or method.get("delayed_market_arrival_delay_ms") is not None\n        or method.get("regular_arrival_delay_ms") is not None\n    ):\n        raise ValueError("PAPER V5 snapshot violates truthful-execution methodology")\n    reconciliation = v5.get("evidence_reconciliation") or {}\n    health = v5.get("execution_health") or {}\n    if reconciliation.get("state") != "PASS" or health.get("state") == "RED":\n        raise ValueError("PAPER V5 snapshot has unreconciled or RED execution evidence")\n'''
if needle not in text:
    raise SystemExit("cloudflare validator tail anchor missing")
text = text.replace(needle, replacement)
p.write_text(text)

p = Path("services/backend/tests/test_paper_v5_r4.py")
text = p.read_text()
text = text.replace(
    '"outcomeIndex": 0,\n',
    '"outcomeIndex": 0,\n        "slug": "r4-market",\n',
)
old = '''    def _get(self, url, params=None):\n        assert url == "https://gamma.test/markets"\n        assert params == {"condition_ids": ["condition-r4"], "limit": 10}\n        return [dict(self.market_data)]\n'''
new = '''    def _get(self, url, params=None):\n        assert url == "https://gamma.test/markets/slug/r4-market"\n        assert params is None\n        return dict(self.market_data)\n'''
if old not in text:
    raise SystemExit("FakeClient gamma anchor missing")
text = text.replace(old, new)
text = text.replace(
    '_market_by_condition(client, "condition-r4")',
    '_market_by_condition(client, "condition-r4", "r4-market")',
)
text = text.replace(
    'client.market_data = {"conditionId": "other"}\n    with pytest.raises(Exception, match="did not match requested condition"):\n        _market_by_condition(client, "condition-r4")',
    'client.market_data = {"conditionId": "other"}\n    with pytest.raises(Exception, match="conditionId mismatch"):\n        _market_by_condition(client, "condition-r4", "r4-market")\n    with pytest.raises(Exception, match="market_slug_unavailable"):\n        _market_by_condition(client, "condition-r4", "")',
)
text = text.replace(
    'assert observed["cohort"] == "PAPER_V5_R4_AUDIT_RECONCILIATION_2026_08_07"',
    'assert observed["cohort"] == "PAPER_V5_R4_1_REPORT_PUBLISH_TRUTH_2026_08_07"',
)
text = text.replace(
    'assert observed["model"] == "L2_TAKER_FAK_ARRIVAL_BOOK_V2_AUDIT_RECONCILED"',
    'assert observed["model"] == "L2_TAKER_FAK_ARRIVAL_BOOK_V3_SLUG_IDENTITY"',
)
extra = r'''

def test_official_delay_is_not_reported_as_synthetic_latency(monkeypatch):
    local = factory()
    client = FakeClient(
        [book(asks=[(0.80, 100)], bids=[(0.79, 100)], suffix="1")],
        market_data=market(secondsDelay=1),
    )
    monkeypatch.setattr("app.paper_v5_r4.time.sleep", lambda _seconds: None)
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5R4(settings(), client).process(db, wallet, activity("0xofficial-delay"))
        execution = db.scalar(select(PaperV5Execution))
        evidence = db.scalar(select(PaperV5ExecutionEvidence))
        assert execution.status == "REJECTED"
        assert execution.simulated_latency_ms == 0
        assert evidence is not None
        assert evidence.official_seconds_delay == 1


def test_r4_1_report_erases_stale_250ms_semantics():
    local = factory()
    with local() as db:
        initialize_state(db, settings())
        baseline = _status_counts(db)
        report = {
            "status": "PASS",
            "run": {"errors": []},
            "methodology": {
                "delayed_market_arrival_delay_basis": "official 250ms CLOB itode window",
                "delayed_market_arrival_delay_ms": 250,
                "regular_arrival_delay_ms": 0,
            },
            "cycle": {"signals_processed": 0},
        }
        reconciled = _apply_r4_report(report, db, baseline)
        method = reconciled["methodology"]
        assert method["delayed_market_arrival_delay_ms"] is None
        assert method["regular_arrival_delay_ms"] is None
        assert "250" not in method["delayed_market_arrival_delay_basis"]
        assert method["immediate_post_fill_marking"] is True
        assert method["end_cycle_mark_refresh"] is True
        assert method["market_identity_exact"] is True
'''
if "test_official_delay_is_not_reported_as_synthetic_latency" not in text:
    text += extra
p.write_text(text)

p = Path("services/backend/tests/test_cloudflare_snapshot_v5.py")
text = p.read_text()
text = text.replace(
    '"PAPER_V5_R4_AUDIT_RECONCILIATION_2026_08_07"',
    '"PAPER_V5_R4_1_REPORT_PUBLISH_TRUTH_2026_08_07"',
)
text = text.replace(
    '"L2_TAKER_FAK_ARRIVAL_BOOK_V2_AUDIT_RECONCILED"',
    '"L2_TAKER_FAK_ARRIVAL_BOOK_V3_SLUG_IDENTITY"',
)
marker = '            "summary_ledger_reconciliation": True,\n'
addition = '''            "unknown_official_delay_fail_closed": True,\n            "market_identity_exact": True,\n            "delayed_market_arrival_delay_ms": None,\n            "regular_arrival_delay_ms": None,\n'''
if marker in text and '"market_identity_exact": True' not in text:
    text = text.replace(marker, marker + addition)
# Ensure fixture has reconciliation and non-red health expected by validator.
needle = '''        "safety": {\n'''
if '"evidence_reconciliation": {"state": "PASS"}' not in text:
    text = text.replace(
        needle,
        '        "evidence_reconciliation": {"state": "PASS"},\n        "execution_health": {"state": "GREEN"},\n' + needle,
        1,
    )
p.write_text(text)
