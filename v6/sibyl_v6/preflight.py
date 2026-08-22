from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

UPSTREAM_SHA = "e35ad881f88c7b5d60388461095ee11b7aa161c5"
TRADING_SECRET_NAMES = {
    "PRIVATE_KEY",
    "LMTS_TOKEN_ID",
    "LMTS_TOKEN_SECRET",
    "LIMITLESS_API_KEY",
    "RELAYER_API_KEY",
    "RELAYER_API_KEY_ADDRESS",
    "POLY_API_KEY",
    "POLY_SECRET",
    "POLY_PASSPHRASE",
    "LIVE_ARMED",
}


@dataclass(frozen=True)
class Preflight:
    DRY_RUN_PREFLIGHT: str
    LIVE_PREFLIGHT: str
    upstream_verified: bool
    live_armed_present: bool
    trading_secrets_present: tuple[str, ...]
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def verify_upstream(root: Path) -> bool:
    marker = root / ".sibyl-upstream-sha"
    strategy = root / "src" / "strategies" / "cross-market-mm"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != UPSTREAM_SHA:
        return False
    if not strategy.is_dir():
        return False
    git_dir = root / ".git"
    if git_dir.exists():
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0 and proc.stdout.strip() == UPSTREAM_SHA
    return True


def dry_run_preflight(upstream_root: Path, env: dict[str, str] | None = None) -> Preflight:
    environment = dict(os.environ if env is None else env)
    secrets = tuple(sorted(name for name in TRADING_SECRET_NAMES if environment.get(name)))
    verified = verify_upstream(upstream_root)
    live_armed = bool(environment.get("LIVE_ARMED"))
    if not verified:
        return Preflight("FAIL", "NOT_RUN", False, live_armed, secrets, "UPSTREAM_SHA_MISMATCH")
    if live_armed:
        return Preflight("FAIL", "NOT_RUN", True, True, secrets, "LIVE_ARMED_FORBIDDEN_R1")
    return Preflight("PASS", "NOT_RUN", True, False, secrets)


def sanitized_upstream_env(env: dict[str, str] | None = None) -> dict[str, str]:
    clean = dict(os.environ if env is None else env)
    for name in TRADING_SECRET_NAMES:
        clean.pop(name, None)
    clean["DRY_RUN"] = "true"
    clean["SIBYL_V6_LIVE_ALLOWED"] = "false"
    return clean


def emit_preflight(path: Path, result: Preflight) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
