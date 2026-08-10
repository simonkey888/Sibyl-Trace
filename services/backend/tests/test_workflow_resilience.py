from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github/workflows/github-paper-v5.yml"


def test_r45_workflow_serializes_rolling_state_and_advances_only_on_success() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "concurrency:\n  group: sibyl-github-paper-v5\n  cancel-in-progress: false" in source

    advance_marker = "- name: Advance private rolling V5 state only on PASS"
    fail_marker = "- name: Fail closed on degraded V5 cycle"
    advance_index = source.index(advance_marker)
    fail_index = source.index(fail_marker)
    assert fail_index > advance_index

    advance_block = source[advance_index:fail_index]
    fail_block = source[fail_index:]
    assert "if: steps.cycle.outcome == 'success'" in advance_block
    assert 'gh release upload "$RELEASE_TAG"' in advance_block
    assert 'git/refs/tags/$RELEASE_TAG' in advance_block
    assert "if: steps.cycle.outcome != 'success'" in fail_block
    assert "canonical rolling state was NOT advanced" in fail_block

    assert source.count('gh release upload "$RELEASE_TAG"') == 1
    assert source.count('git/refs/tags/$RELEASE_TAG') == 1
