#!/usr/bin/env python3
"""Check the documented local edition artifact, without reading provider logs.

The Codex task's current run status is not exposed by a repository contract.
A current local edition is not proof of a successful push, deploy or live page.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path


TASK_HINT = "Check the Codex task qdaily-today-s-pick for its current run result."


def pick_problem(root: Path, today: date) -> str | None:
    """Return an observed freshness problem, or None for today's local output."""
    try:
        data = json.loads((root / "data/daily_picks.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return f"Today's Pick local output is missing (expected {today}). {TASK_HINT}"
    except (OSError, ValueError, UnicodeError):
        return f"Today's Pick local output is unreadable (expected {today}). {TASK_HINT}"

    value = data.get("date") if isinstance(data, dict) else None
    try:
        edition = date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        edition = None
    if edition is None or value != edition.isoformat():
        return f"Today's Pick local output has an invalid date (expected {today}). {TASK_HINT}"
    if edition < today:
        return f"Today's Pick local output is stale (edition {edition}; expected {today}). {TASK_HINT}"
    if edition > today:
        return f"Today's Pick local output has a future date ({edition}; expected {today}). {TASK_HINT}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    # The caller supplies its Asia/Shanghai date, also used for alert deduplication.
    parser.add_argument("--today", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    problem = pick_problem(args.root, args.today)
    if problem:
        print(problem)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
