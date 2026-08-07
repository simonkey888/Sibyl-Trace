from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research_dashboard import render_research_dashboard


def render_from_file(report_path: Path, output_path: Path) -> None:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("trial report must be a JSON object")
    output_path.write_text(render_research_dashboard(payload), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Sibyl PAPER V2 static research dashboard")
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    render_from_file(args.report, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
