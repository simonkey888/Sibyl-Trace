from pathlib import Path

p = Path("services/backend/tests/test_paper_v5_r4.py")
text = p.read_text()
if "from pathlib import Path" not in text:
    text = text.replace("import json\n", "import json\nfrom pathlib import Path\n")
text = text.replace(
    "    _rules_from_official_metadata,\n",
    "    _rules_from_official_metadata,\n    run as run_r4,\n",
)
extra = r'''

def test_run_wrapper_installs_and_restores_r4_contract(monkeypatch, tmp_path):
    local = factory()
    original_cohort = legacy.COHORT_ID
    original_engine = legacy.PaperEngineV5
    original_build = legacy.build_report
    original_writer = legacy._write_ledger
    original_model = legacy.EXECUTION_MODEL
    observed = {}

    monkeypatch.setattr(legacy, "init_db", lambda: None)
    monkeypatch.setattr(legacy, "SessionLocal", local)

    def fake_run(output_dir: Path):
        observed["output_dir"] = output_dir
        observed["cohort"] = legacy.COHORT_ID
        observed["engine"] = legacy.PaperEngineV5
        observed["model"] = legacy.EXECUTION_MODEL
        assert legacy.build_report is not original_build
        assert legacy._write_ledger is not original_writer
        return 0

    monkeypatch.setattr(legacy, "run", fake_run)
    assert run_r4(tmp_path) == 0
    assert observed["output_dir"] == tmp_path
    assert observed["cohort"] == "PAPER_V5_R4_AUDIT_RECONCILIATION_2026_08_07"
    assert observed["engine"] is PaperEngineV5R4
    assert observed["model"] == "L2_TAKER_FAK_ARRIVAL_BOOK_V2_AUDIT_RECONCILED"
    assert legacy.COHORT_ID == original_cohort
    assert legacy.PaperEngineV5 is original_engine
    assert legacy.build_report is original_build
    assert legacy._write_ledger is original_writer
    assert legacy.EXECUTION_MODEL == original_model
'''
if "test_run_wrapper_installs_and_restores_r4_contract" not in text:
    text += extra
p.write_text(text)
