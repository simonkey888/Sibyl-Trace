from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github/workflows/github-paper-v5.yml"


def test_r45_workflow_serializes_one_non_resetting_rolling_state_transition() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "concurrency:\n  group: sibyl-github-paper-v5\n  cancel-in-progress: false" in source
    assert "reset_state" not in source
    assert "push:" not in source
    assert 'gh release upload "$RELEASE_TAG"' in source
    assert "git/refs/tags/$RELEASE_TAG" in source
    assert source.count('gh release upload "$RELEASE_TAG"') == 1
    assert source.count("git/refs/tags/$RELEASE_TAG") == 1

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


def test_rolling_state_requires_exact_head_ci_before_any_release_mutation() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    gate = source.index("Require exact-head CI before any rolling-state transition")
    restore = source.index("Restore verified V5 rolling state")
    advance = source.index("Advance rolling V5 state with non-empty release assets only")
    assert gate < restore < advance
    assert "python -m app.ci_gate" in source
    assert '[[ "$SOURCE_SHA" == "$MAIN_SHA" ]]' in source
    advance_block = source[advance:]
    assert "TRANSITIONS.json" in advance_block
    assert "CANONICAL_STATE_TRANSITION=IDEMPOTENT_NOOP" in advance_block
    assert "transition_key" in advance_block


def test_code_change_transition_is_bound_to_exact_green_ci_run() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert '[[ "${{ inputs.expected_source_sha }}" == "$SOURCE_SHA" ]]' in source
    assert '[[ "${{ inputs.source_ci_run_id }}" == "$CI_RUN_ID" ]]' in source
    assert 'TRANSITION_KEY="ci:${CI_RUN_ID}:${SOURCE_SHA}"' in source


def test_order_scaffolding_and_duplicate_writers_are_removed() -> None:
    workflows = REPO_ROOT / ".github/workflows"
    assert not (workflows / "order-001-pytest-diagnostic.yml").exists()
    assert not (workflows / "order-001-candidate.yml").exists()
    assert not (workflows / "trigger-paper-v5-on-push.yml").exists()
    assert not (workflows / "dispatch-paper-v5-on-label.yml").exists()
    candidate = (workflows / "paper-v5-candidate.yml").read_text(encoding="utf-8")
    assert "actions: read" in candidate
    assert "contents: read" in candidate
    assert "contents: write" not in candidate
    assert "actions: write" not in candidate
    assert "wrangler deploy" not in candidate
    assert "gh release" not in candidate
    assert "SHA256SUMS" in candidate
    assert "PUBLIC_SNAPSHOT_DRY_VERIFY=PASS" in candidate


def test_ci_has_no_order_specific_branch_trigger_and_dispatches_verified_identity() -> None:
    ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert '"order-001-**"' not in ci
    assert "transition_kind=code-change" in ci
    assert 'expected_source_sha="$GITHUB_SHA"' in ci
    assert 'source_ci_run_id="$GITHUB_RUN_ID"' in ci


def test_oos_preregistration_uses_two_phase_read_only_github_server_metadata():
    prereg = (REPO_ROOT / ".github/workflows/paper-v5-oos-preregister.yml").read_text(
        encoding="utf-8"
    )
    finalizer = (REPO_ROOT / ".github/workflows/paper-v5-oos-registration-finalize.yml").read_text(
        encoding="utf-8"
    )
    for workflow in (prereg, finalizer):
        assert "actions: read" in workflow
        assert "contents: read" in workflow
        assert "contents: write" not in workflow
        assert "actions: write" not in workflow
        assert "wrangler deploy" not in workflow
        assert "gh release" not in workflow
    assert "workflow_dispatch:" in prereg
    assert "PAPER V5 OOS Preregistration" in finalizer
    assert "workflow_run:" in finalizer
    assert "conclusion == 'success'" in finalizer
    assert "/actions/runs/$PREREG_RUN_ID" in finalizer
    assert "/actions/runs/$PREREG_RUN_ID/artifacts" in finalizer
    assert "build_github_oos_registration_proof" in finalizer
    assert "registration_created_at < cohort.selection_cutoff" in finalizer
    assert "run['status']" not in finalizer
    assert "run['conclusion']" not in finalizer
