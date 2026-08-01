#!/usr/bin/env python3
"""
Freeze a completed calendar month into archive/YYYY-MM.json.

Layer 3 (execution). Run nightly; it is idempotent and skips months already frozen
unless --force is passed.

Why this exists: Claude Code prunes transcripts (90 days here, 30 by default). Once
a month is archived, its numbers survive forever even though the raw JSONL is gone.
That is what makes month-over-month comparison and any long-run history possible.

  python3 archive.py                # freeze every completed month not yet archived
  python3 archive.py 2026-07        # freeze one specific month
  python3 archive.py 2026-07 --force
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from count import (RETENTION_RAISED, ROOT, Window, apply_patterns, load_cfg,
                   plan_info, scan)


def retention_days():
    """How far back raw transcripts can possibly go, per Claude Code's setting."""
    try:
        cfg = json.loads((Path.home() / ".claude" / "settings.json").read_text())
        return int(cfg.get("cleanupPeriodDays", 30))
    except (OSError, ValueError, TypeError):
        return 30


def month_bounds(year, month):
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = (datetime(year + (month == 12), (month % 12) + 1, 1, tzinfo=timezone.utc))
    return start, end


def freeze(year, month, rates, patterns, force=False):
    tag = f"{year:04d}-{month:02d}"
    dest = ROOT / "archive" / f"{tag}.json"
    if dest.exists() and not force:
        return None

    start, end = month_bounds(year, month)
    now = datetime.now(timezone.utc)
    if end > now:
        return None  # month not finished yet

    w = Window(start, end)
    files = scan({"m": w}, start)

    # Months with no activity at all are not worth a file. Writing them creates a
    # wall of $0 archives that look like data loss rather than absence of use.
    if not w.human_texts and not w.tokens:
        return None

    # A month is only trustworthy if the pruner could not have reached into it.
    # Anything starting before we raised retention, and older than the always-kept
    # 30 days, is partial - archive it but flag it so the card never compares to it.
    complete = w.complete(now)

    plan = plan_info(rates)
    cost = w.cost(rates)
    tok = w.totals()
    msgs = len(w.human_texts)

    payload = {
        "period": tag,
        "kind": "calendar_month",
        "complete": complete,
        "frozen_at": now.isoformat(),
        "scan": {"files_read": files},
        "me": {"messages": msgs},
        "claude": {
            "tool_calls": sum(w.tools.values()),
            "agents_spawned": w.tools.get("Agent", 0),
            "lines_written": w.lines_added,
            "lines_removed": w.lines_removed,
            "file_edits": sum(w.tools.get(k, 0) for k in ("Edit", "Write", "NotebookEdit")),
            "commits": w.commits,
        },
        "activity": {
            "sessions": len(w.sessions),
            "active_days": len(w.days),
            "top_projects": w.projects.most_common(5),
        },
        "tokens": {
            "input": tok["input_tokens"],
            "output": tok["output_tokens"],
            "cache_write": tok["cache_creation_input_tokens"],
            "cache_read": tok["cache_read_input_tokens"],
        },
        "models": {m: dict(t) for m, t in w.tokens.items()},
        "cost": {
            "api_equivalent_usd": round(cost, 2),
            "plan_label": plan["label"],
            "plan_monthly_usd": plan["monthly"],
        },
        "phrases": apply_patterns(w, patterns),
    }

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2))
    flag = "" if complete else "  [PARTIAL - pruned before retention was raised]"
    print(f"froze {dest.name}: {msgs} msgs, ${cost:,.0f}{flag}")
    return payload


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    rates, patterns = load_cfg("rates.yml"), load_cfg("patterns.yml")

    if args:
        y, m = args[0].split("-")
        freeze(int(y), int(m), rates, patterns, force)
        return

    # Only walk back as far as retention could possibly hold raw transcripts.
    # Each month costs a full scan (~5s), so the old blanket 13-month walk spent
    # over a minute re-proving that months with no data on disk have no data.
    now = datetime.now(timezone.utc)
    months_back = max(2, -(-retention_days() // 28) + 1)
    cursor = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    wrote = empty = 0
    for _ in range(months_back):
        cursor = (cursor - timedelta(days=1)).replace(day=1)
        if freeze(cursor.year, cursor.month, rates, patterns, force):
            wrote += 1
            empty = 0
        else:
            empty += 1
            if empty >= 2:
                break   # two silent months in a row: nothing older survives
    if not wrote:
        print("nothing new to archive")


if __name__ == "__main__":
    main()
