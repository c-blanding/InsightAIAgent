"""Dump the timeline + action log for a LangGraph thread.

Usage:
  .\\.venv\\Scripts\\python.exe -m insightai.evidence.timeline_cli <thread_id>
  .\\.venv\\Scripts\\python.exe -m insightai.evidence.timeline_cli <thread_id> --pretty
"""

from __future__ import annotations

import argparse
import json
import sys

from dotenv import load_dotenv

from insightai.evidence.timeline import assemble_timeline
from insightai.runtime_paths import REPO_ROOT

load_dotenv(REPO_ROOT / ".env")


def main() -> int:
    parser = argparse.ArgumentParser(description="Print run timeline / action log JSON")
    parser.add_argument("thread_id", help="LangGraph thread_id")
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Indent JSON output",
    )
    parser.add_argument(
        "--actions-only",
        action="store_true",
        help="Print only tool action events",
    )
    args = parser.parse_args()

    timeline = assemble_timeline(args.thread_id)
    data = timeline.model_dump(mode="json")
    if args.actions_only:
        data = {
            "thread_id": timeline.thread_id,
            "actions": [item.model_dump(mode="json") for item in timeline.actions],
            "error": timeline.error,
        }
    print(json.dumps(data, indent=2 if args.pretty else None, default=str))
    return 0 if not timeline.error else 1


if __name__ == "__main__":
    raise SystemExit(main())
