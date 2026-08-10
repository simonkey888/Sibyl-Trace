from pathlib import Path

from app.watchdogs import ACCOUNTING_WATCHDOG


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github/workflows"


def test_r45_workflow_accepts_present_empty_ledger_as_cold_start_evidence() -> None:
    workflow = (WORKFLOWS / "github-paper-v5.yml").read_text(encoding="utf-8")
    assert 'empty_valid = name == "prediction-ledger-v5.jsonl"' in workflow
    assert "included = path.is_file() and (empty_valid or path.stat().st_size > 0)" in workflow
    assert 'assert packaged["prediction-ledger-v5.jsonl"]["empty_valid"] is True' in workflow


def test_paper_v2_uses_shared_neutral_accounting_watchdog_identity() -> None:
    source = (REPO_ROOT / "services/backend/app/paper_v2.py").read_text(encoding="utf-8")
    assert "from app.watchdogs import ACCOUNTING_WATCHDOG, accounting_watchdog" in source
    assert '"watchdog": ACCOUNTING_WATCHDOG' in source
    assert "ACCOUNTING_RECONCILIATION_FAILURE" not in source
    assert ACCOUNTING_WATCHDOG == "ACCOUNTING_RECONCILIATION"


def test_only_r45_publisher_can_write_cloudflare_worker() -> None:
    writers: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        source = path.read_text(encoding="utf-8")
        if "wrangler deploy" in source:
            writers.append(path.name)
    assert writers == ["publish-cloudflare-terminal-v5.yml"]


def test_legacy_cloudflare_paths_are_retired_without_credentials() -> None:
    retired = (
        "publish-cloudflare-terminal.yml",
        "publish-cloudflare-terminal-v4.yml",
        "deploy-cloudflare.yml",
    )
    for name in retired:
        source = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "wrangler deploy" not in source
        assert "CLOUDFLARE_API_TOKEN" not in source
        assert "CLOUDFLARE_ACCOUNT_ID" not in source
        assert "canonical public publisher" in source.lower() or "only publish-cloudflare-terminal-v5.yml" in source.lower()


def test_r45_publisher_rejects_stale_successful_source_sha() -> None:
    source = (WORKFLOWS / "publish-cloudflare-terminal-v5.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" not in source
    assert 'main_sha=$(gh api "/repos/$GITHUB_REPOSITORY/git/ref/heads/main"' in source
    assert 'if [[ "$sha" != "$main_sha" ]]' in source
    assert "Refusing stale V5 publish" in source
    assert '"canonical_publisher_workflow": "publish-cloudflare-terminal-v5.yml"' in source
    assert '"max_public_snapshot_age_seconds": 10800' in source


def test_legacy_paper_v2_is_not_a_scheduled_or_writable_truth_source() -> None:
    source = (WORKFLOWS / "github-paper-trial.yml").read_text(encoding="utf-8")
    assert "schedule:" not in source
    assert "push:" not in source
    assert "issue_comment:" not in source
    assert "contents: write" not in source
    assert "PAPER V2 no longer runs on push, cron, or issue comments" in source
    assert "Canonical scheduled PAPER workflow: github-paper-v5.yml" in source
