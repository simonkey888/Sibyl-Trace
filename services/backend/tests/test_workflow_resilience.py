from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github/workflows/github-paper-v5.yml"


def test_r45_workflow_serializes_rolling_state_and_advances_only_after_pass() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "concurrency:\n  group: sibyl-github-paper-v5\n  cancel-in-progress: false" in source

    cycle = source.index("- name: Execute bounded PAPER V5 cycle")
    validate = source.index("- name: Validate and package evidence")
    upload = source.index("- name: Upload immutable V5 audit artifact")
    advance = source.index("- name: Advance rolling V5 state with non-empty release assets only")
    confirm = source.index("- name: Confirm rolling state is readable")
    assert cycle < validate < upload < advance < confirm

    # GitHub's default step success() semantics must remain intact: a failed
    # execution or validation step cannot fall through to the release mutation.
    guarded_path = source[cycle:advance]
    assert "continue-on-error: true" not in guarded_path
    assert 'grep -q \'"state": "PASS"\'' in guarded_path
    assert '! grep -q \'"state": "RED"\'' in guarded_path

    advance_block = source[advance:confirm]
    assert 'test -s "$OUTPUT_DIR/sibyl-v5.db.gz"' in advance_block
    assert 'gh release upload "$RELEASE_TAG"' in advance_block
    assert 'git/refs/tags/$RELEASE_TAG' in advance_block
    assert source.count('gh release upload "$RELEASE_TAG"') == 1
    assert source.count('git/refs/tags/$RELEASE_TAG') == 1
