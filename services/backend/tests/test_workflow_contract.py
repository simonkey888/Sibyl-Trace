from pathlib import Path

from app.watchdogs import ACCOUNTING_WATCHDOG


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_r45_workflow_accepts_present_empty_ledger_as_cold_start_evidence() -> None:
    workflow = (REPO_ROOT / ".github/workflows/github-paper-v5.yml").read_text(
        encoding="utf-8"
    )
    assert 'empty_valid = name == "prediction-ledger-v5.jsonl"' in workflow
    assert "included = path.is_file() and (empty_valid or path.stat().st_size > 0)" in workflow
    assert 'assert packaged["prediction-ledger-v5.jsonl"]["empty_valid"] is True' in workflow


def test_paper_v2_uses_shared_neutral_accounting_watchdog_identity() -> None:
    source = (REPO_ROOT / "services/backend/app/paper_v2.py").read_text(encoding="utf-8")
    assert "from app.watchdogs import ACCOUNTING_WATCHDOG, accounting_watchdog" in source
    assert '"watchdog": ACCOUNTING_WATCHDOG' in source
    assert "ACCOUNTING_RECONCILIATION_FAILURE" not in source
    assert ACCOUNTING_WATCHDOG == "ACCOUNTING_RECONCILIATION"
