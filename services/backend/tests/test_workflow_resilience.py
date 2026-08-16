from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github/workflows/github-paper-v5.yml"


def test_r45_workflow_serializes_one_non_resetting_rolling_state_transition() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert (
        "concurrency:\n  group: sibyl-github-paper-v5\n  cancel-in-progress: false"
        in source
    )
    assert "reset_state" not in source
    assert "push:" not in source
    assert 'gh release upload "$RELEASE_TAG"' in source
    assert 'git/refs/tags/$RELEASE_TAG' in source
    assert source.count('gh release upload "$RELEASE_TAG"') == 1
    assert source.count('git/refs/tags/$RELEASE_TAG') == 1

    validate_marker = "- name: Validate and package evidence"
    advance_marker = "- name: Advance rolling V5 state with non-empty release assets only"
    confirm_marker = "- name: Confirm rolling state is readable"
    validate_index = source.index(validate_marker)
    advance_index = source.index(advance_marker)
    confirm_index = source.index(confirm_marker)
    assert validate_index < advance_index < confirm_index

    advance_block = source[advance_index:confirm_index]
    assert 'test -s "$OUTPUT_DIR/sibyl-v5.db.gz"' in advance_block
    assert 'gh release upload "$RELEASE_TAG"' in advance_block
