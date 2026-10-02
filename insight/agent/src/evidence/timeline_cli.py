"""Dump the timeline + action log for a LangGraph thread.

Usage:
  .\\.venv\\Scripts\\python.exe insight\\agent\\src\\evidence\\timeline_cli.py <thread_id>
  .\\.venv\\Scripts\\python.exe insight\\agent\\src\\evidence\\timeline_cli.py <thread_id> --pretty
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[4] / ".env")


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

    from evidence.timeline import assemble_timeline

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
