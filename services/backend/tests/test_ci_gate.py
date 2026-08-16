import pytest

from app.ci_gate import REQUIRED_CI_JOBS, verify_required_ci_payload

SHA = "a" * 40


def jobs(*, failure: str | None = None, skipped: str | None = None):
    rows = []
    for name in REQUIRED_CI_JOBS:
        conclusion = "success"
        if name == failure:
            conclusion = "failure"
        if name == skipped:
            conclusion = "skipped"
        rows.append({"name": name, "conclusion": conclusion})
    return rows


def run(run_id: int = 1, *, sha: str = SHA, conclusion: str = "success"):
    return {
        "id": run_id,
        "name": "CI",
        "event": "push",
        "head_sha": sha,
        "status": "completed",
        "conclusion": conclusion,
    }


def test_exact_sha_all_required_green_passes():
    result = verify_required_ci_payload(
        source_sha=SHA,
        runs=[run()],
        jobs_by_run={1: jobs()},
    )
    assert result["status"] == "PASS"
    assert result["source_sha"] == SHA
    assert result["ci_run_id"] == 1


@pytest.mark.parametrize("bad", REQUIRED_CI_JOBS)
def test_any_required_red_job_fails_closed(bad: str):
    with pytest.raises(ValueError, match="jobs_not_all_green"):
        verify_required_ci_payload(
            source_sha=SHA,
            runs=[run()],
            jobs_by_run={1: jobs(failure=bad)},
        )


def test_skipped_required_job_fails_closed():
    with pytest.raises(ValueError, match="jobs_not_all_green"):
        verify_required_ci_payload(
            source_sha=SHA,
            runs=[run()],
            jobs_by_run={1: jobs(skipped="safety")},
        )


def test_stale_sha_cannot_authorize_publication():
    with pytest.raises(ValueError, match="green_ci_missing"):
        verify_required_ci_payload(
            source_sha=SHA,
            runs=[run(sha="b" * 40)],
            jobs_by_run={1: jobs()},
        )


def test_red_ci_conclusion_cannot_authorize_publication():
    with pytest.raises(ValueError, match="green_ci_missing"):
        verify_required_ci_payload(
            source_sha=SHA,
            runs=[run(conclusion="failure")],
            jobs_by_run={1: jobs()},
        )
