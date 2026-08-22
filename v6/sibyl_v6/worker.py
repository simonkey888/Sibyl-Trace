from __future__ import annotations

import json
import os
import sys
import time

from .runner import main as run_cycle


def main() -> int:
    try:
        interval = max(30, int(os.environ.get("SIBYL_V6_CYCLE_INTERVAL_SECONDS", "60")))
    except ValueError:
        raise SystemExit("SIBYL_V6_CYCLE_INTERVAL_SECONDS_INVALID")

    cycle = 0
    while True:
        cycle += 1
        started = int(time.time() * 1000)
        code = int(run_cycle())
        print(
            json.dumps(
                {
                    "event": "v6_worker_cycle_complete",
                    "cycle": cycle,
                    "started_at_ms": started,
                    "completed_at_ms": int(time.time() * 1000),
                    "result_code": code,
                    "LIVE": "NO",
                    "REAL_ORDERS": 0,
                    "CAPITAL_MOVED_USD": "0",
                },
                sort_keys=True,
            ),
            flush=True,
        )
        # Integrity/preflight failure is not transient. Fail closed instead of
        # repeatedly running an image whose provenance cannot be established.
        if code == 2:
            return code
        # R1 has upstream execution disabled. Any unexpected non-feed failure
        # is also terminal; feed transport failure (3) is observable/retryable.
        if code not in (0, 3):
            return code
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
