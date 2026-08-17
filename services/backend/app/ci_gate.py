from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

REQUIRED_CI_JOBS = (
    "backend",
    "dashboard",
    "safety",
    "container",
    "research-evidence",
    "scoring-integrity",
)


def verify_required_ci_payload(
    *,
    source_sha: str,
    runs: list[dict[str, Any]],
    jobs_by_run: dict[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    candidates = [
        row
        for row in runs
        if str(row.get("head_sha") or "") == source_sha
        and row.get("name") == "CI"
        and row.get("event") == "push"
        and row.get("status") == "completed"
        and row.get("conclusion") == "success"
    ]
    candidates.sort(key=lambda row: int(row.get("id") or 0), reverse=True)
    if not candidates:
        raise ValueError("required_exact_sha_green_ci_missing")

    for run in candidates:
        run_id = int(run.get("id") or 0)
        jobs = jobs_by_run.get(run_id, [])
        conclusions = {
            str(job.get("name") or ""): str(job.get("conclusion") or "")
            for job in jobs
        }
        missing = [name for name in REQUIRED_CI_JOBS if name not in conclusions]
        failed = [
            name
            for name in REQUIRED_CI_JOBS
            if conclusions.get(name) not in {None, "success"}
        ]
        if not missing and not failed:
            return {
                "status": "PASS",
                "source_sha": source_sha,
                "ci_run_id": run_id,
                "required_jobs": list(REQUIRED_CI_JOBS),
                "job_conclusions": {
                    name: conclusions[name] for name in REQUIRED_CI_JOBS
                },
            }
    raise ValueError("required_exact_sha_ci_jobs_not_all_green")


def _github_json(url: str, token: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "sibyl-trace-ci-gate",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"github_ci_gate_transport_failed:{type(exc).__name__}") from exc


def verify_github_required_ci(
    *,
    repository: str,
    source_sha: str,
    token: str,
) -> dict[str, Any]:
    if len(source_sha) != 40:
        raise ValueError("source_sha_invalid")
    if not token:
        raise ValueError("github_token_required")
    encoded_sha = urllib.parse.quote(source_sha, safe="")
    runs_url = (
        f"https://api.github.com/repos/{repository}/actions/workflows/ci.yml/runs"
        f"?head_sha={encoded_sha}&event=push&per_page=20"
    )
    payload = _github_json(runs_url, token)
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        raise ValueError("github_ci_runs_shape_invalid")

    jobs_by_run: dict[int, list[dict[str, Any]]] = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        if str(run.get("head_sha") or "") != source_sha:
            continue
        run_id = int(run.get("id") or 0)
        if run_id <= 0:
            continue
        jobs_payload = _github_json(
            f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/jobs?per_page=100",
            token,
        )
        jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else None
        if isinstance(jobs, list):
            jobs_by_run[run_id] = [job for job in jobs if isinstance(job, dict)]

    return verify_required_ci_payload(
        source_sha=source_sha,
        runs=[run for run in runs if isinstance(run, dict)],
        jobs_by_run=jobs_by_run,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify exact-SHA required GitHub CI before public publication"
    )
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", ""))
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = verify_github_required_ci(
        repository=args.repository,
        source_sha=args.source_sha,
        token=os.getenv("GH_TOKEN", ""),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
