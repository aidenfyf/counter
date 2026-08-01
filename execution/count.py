#!/usr/bin/env python3
"""
Scan Claude Code transcripts and emit stats.json for the counter card.

Layer 3 (execution). Deterministic, no network, no LLM.

Reads   ~/.claude/projects/**/*.jsonl   (session transcripts)
        ~/.claude.json                   (subscription tier)
        config/patterns.yml              (phrase counters)
        config/rates.yml                 (pricing + plan prices)
Writes  out/stats.json

Window is a rolling N days (default 30), NOT a calendar month. The transcripts on
disk are themselves pruned on a rolling basis, so a rolling window can never claim
data the pruner already took, and the card is never half-empty on the 1st.

Counting rules that matter, learned the hard way:
  * A genuine human turn is  type=="user" AND origin.kind=="human".
    Everything else that looks like a user message is machine noise: tool results,
    "[Image: ...]" stubs, <command-name> slash expansions, <bash-stdout>,
    context-continuation summaries, hook feedback.
  * Cost and tool calls INCLUDE subagent sidechains. Agents spend real tokens on
    your behalf; excluding them under-reports by roughly a third.
  * Sum tokens across the whole window first, then apply rates ONCE. Costing each
    message and adding up truncates tens of thousands of times and drifts ~2%.
"""

import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = Path.home() / ".claude" / "projects"
CLAUDE_JSON = Path.home() / ".claude.json"
WINDOW_DAYS = int(os.environ.get("COUNTER_WINDOW_DAYS", "30"))

# Retention was raised from the 30-day default to 90 on this date. Windows starting
# before it cannot be trusted as complete, because the pruner had already eaten the
# tail. Used to decide whether the comparison delta is allowed to render at all.
RETENTION_RAISED = datetime(2026, 7, 31, tzinfo=timezone.utc)


def load_cfg(name):
    with open(ROOT / "config" / name) as fh:
        return yaml.safe_load(fh)


def text_of(message):
    """Flatten a message's content to plain text, ignoring tool blocks."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def candidate_files(oldest_needed):
    """
    Prefilter by mtime so retention length never drives scan time. A session file
    is written as it grows, so its mtime is at or after its newest row; a two-day
    pad covers long-running sessions and clock skew.
    """
    cutoff = (oldest_needed - timedelta(days=2)).timestamp()
    for path in PROJECTS.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime >= cutoff:
                yield path
        except OSError:
            continue


def plan_info(rates):
    """Read the live subscription tier off disk. Never hardcode the plan."""
    unknown = {"tier": None, "label": "subscription", "monthly": None}
    try:
        acct = json.loads(CLAUDE_JSON.read_text()).get("oauthAccount") or {}
    except (OSError, ValueError):
        return unknown
    tier = acct.get("organizationRateLimitTier")
    if not tier:
        return unknown
    plan = (rates.get("plans") or {}).get(tier)
    if not plan:
        # Unknown tier: still name it rather than silently claiming a price.
        return {"tier": tier, "label": tier, "monthly": None}
    return {"tier": tier, "label": plan["label"], "monthly": plan["monthly"]}


class Window:
    """One time window's accumulators."""

    def __init__(self, start, end):
        self.start, self.end = start, end
        self.human_texts = []
        self.claude_texts = []
        self.tokens = defaultdict(Counter)   # model -> token counters
        self.tools = Counter()               # tool name -> calls
        self.sessions = set()
        self.days = set()
        self.projects = Counter()
        self.lines_added = 0
        self.lines_removed = 0
        self.commits = 0

    def contains(self, ts):
        return self.start <= ts < self.end

    def complete(self, now):
        """
        A window is trustworthy only if the pruner cannot have touched it: either it
        sits inside the always-retained last 30 days, or it starts after the day we
        raised retention.
        """
        return self.start >= (now - timedelta(days=30)) or self.start >= RETENTION_RAISED

    def cost(self, rates):
        """Sum tokens first, apply rates once. Returns USD."""
        cw = rates["cache_write_multiplier"]
        cr = rates["cache_read_multiplier"]
        total = 0.0
        for model, tok in self.tokens.items():
            rate = rates["models"].get(model)
            if not rate:
                continue  # unpriced or synthetic model: skip rather than guess
            i, o = rate["input"], rate["output"]
            total += (
                tok["input_tokens"] * i
                + tok["output_tokens"] * o
                + tok["cache_creation_input_tokens"] * i * cw
                + tok["cache_read_input_tokens"] * i * cr
            ) / 1e6
        return total

    def totals(self):
        t = Counter()
        for tok in self.tokens.values():
            t.update(tok)
        return t


def scan(windows, oldest_needed):
    TOK = ("input_tokens", "output_tokens",
           "cache_creation_input_tokens", "cache_read_input_tokens")
    files = 0
    for path in candidate_files(oldest_needed):
        files += 1
        try:
            fh = open(path, errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                # Cheap reject before paying for JSON parsing.
                if '"type":"user"' not in line and '"type":"assistant"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                raw_ts = row.get("timestamp", "")
                if not raw_ts:
                    continue
                try:
                    ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                except ValueError:
                    continue

                hits = [w for w in windows.values() if w.contains(ts)]
                if not hits:
                    continue

                msg = row.get("message") or {}
                kind = row.get("type")

                if kind == "user":
                    origin = row.get("origin") or {}
                    if origin.get("kind") != "human" or row.get("isSidechain"):
                        continue
                    body = text_of(msg)
                    for w in hits:
                        w.human_texts.append(body)
                        w.days.add(raw_ts[:10])
                        w.projects[Path(row.get("cwd") or "?").name] += 1
                        if row.get("sessionId"):
                            w.sessions.add(row["sessionId"])
                    continue

                # assistant: cost, tools and churn, subagents included
                usage = msg.get("usage") or {}
                model = msg.get("model")
                content = msg.get("content")
                body = text_of(msg)

                for w in hits:
                    w.claude_texts.append(body)
                    w.days.add(raw_ts[:10])
                    if model:
                        for k in TOK:
                            w.tokens[model][k] += usage.get(k, 0) or 0

                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name")
                    inp = block.get("input") or {}
                    added = removed = 0
                    if name in ("Edit", "Write", "NotebookEdit"):
                        new = inp.get("new_string") or inp.get("content") or inp.get("new_source") or ""
                        old = inp.get("old_string") or ""
                        added = new.count("\n") + 1 if new else 0
                        removed = old.count("\n") + 1 if old else 0
                    is_commit = name == "Bash" and "git commit" in (inp.get("command") or "")
                    for w in hits:
                        w.tools[name] += 1
                        w.lines_added += added
                        w.lines_removed += removed
                        if is_commit:
                            w.commits += 1
    return files


def apply_patterns(window, patterns):
    out = {}
    for spec in patterns["counters"]:
        rx = re.compile(spec["regex"], re.I)
        corpus = window.human_texts if spec["who"] == "me" else window.claude_texts
        if spec["mode"] == "turns":
            n = sum(1 for t in corpus if rx.search(t))
        else:
            n = sum(len(rx.findall(t)) for t in corpus)
        # "1 in N" is only meaningful for my own turns. Claude emits tens of thousands
        # of assistant messages per window, so "1 in 630" there is noise, not a stat.
        ratio = None
        if spec["who"] == "me" and spec["mode"] == "turns" and n:
            ratio = round(len(corpus) / n, 1)
        out[spec["key"]] = {
            "label": spec["label"],
            "who": spec["who"],
            "count": n,
            "one_in": ratio,
        }
    return out


def main():
    started = time.time()
    rates = load_cfg("rates.yml")
    patterns = load_cfg("patterns.yml")

    now = datetime.now(timezone.utc)
    win = timedelta(days=WINDOW_DAYS)
    windows = {
        "current": Window(now - win, now),
        "prior": Window(now - 2 * win, now - win),
    }
    files = scan(windows, now - 2 * win)

    cur, prior = windows["current"], windows["prior"]
    plan = plan_info(rates)
    cost = cur.cost(rates)
    tok = cur.totals()
    msgs = len(cur.human_texts)
    tools = sum(cur.tools.values())

    # The delta is allowed to render only when BOTH windows are trustworthy.
    # Shown against a pruned prior window it reads +2000% and discredits the card.
    prior_cost = prior.cost(rates)
    can_compare = cur.complete(now) and prior.complete(now)
    delta = None
    if can_compare and prior_cost > 0:
        delta = {
            "cost_pct": round((cost - prior_cost) / prior_cost * 100, 1),
            "msgs_pct": round(
                (msgs - len(prior.human_texts)) / max(len(prior.human_texts), 1) * 100, 1
            ),
            "prior_cost_usd": round(prior_cost, 2),
        }

    stats = {
        "generated_at": now.isoformat(),
        "window": {
            "days": WINDOW_DAYS,
            "start": cur.start.isoformat(),
            "end": cur.end.isoformat(),
            "label": f"LAST {WINDOW_DAYS} DAYS",
            "complete": cur.complete(now),
        },
        "scan": {"files_read": files, "seconds": round(time.time() - started, 2)},
        "me": {
            "messages": msgs,
            "median_chars": (
                sorted(len(t) for t in cur.human_texts)[msgs // 2] if msgs else 0
            ),
        },
        "claude": {
            "tool_calls": tools,
            "agents_spawned": cur.tools.get("Agent", 0),
            "top_tools": cur.tools.most_common(8),
            "lines_written": cur.lines_added,
            "lines_removed": cur.lines_removed,
            "file_edits": sum(cur.tools.get(k, 0) for k in ("Edit", "Write", "NotebookEdit")),
            "commits": cur.commits,
        },
        "activity": {
            "sessions": len(cur.sessions),
            "active_days": len(cur.days),
            "top_projects": cur.projects.most_common(5),
            "tools_per_message": round(tools / msgs, 1) if msgs else 0,
        },
        "tokens": {
            "input": tok["input_tokens"],
            "output": tok["output_tokens"],
            "cache_write": tok["cache_creation_input_tokens"],
            "cache_read": tok["cache_read_input_tokens"],
        },
        "models": {m: dict(t) for m, t in cur.tokens.items()},
        "cost": {
            "api_equivalent_usd": round(cost, 2),
            "plan_label": plan["label"],
            "plan_monthly_usd": plan["monthly"],
            "saved_usd": round(cost - plan["monthly"], 2) if plan["monthly"] else None,
            "multiple": round(cost / plan["monthly"], 1) if plan["monthly"] else None,
        },
        "phrases": apply_patterns(cur, patterns),
        "comparison": delta,   # null until both windows are provably complete
    }

    out = ROOT / "out" / "stats.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stats, indent=2))
    print(f"wrote {out}  ({stats['scan']['seconds']}s, {files} files)")
    return stats


if __name__ == "__main__":
    s = main()
    c = s["cost"]
    print(
        f"  {s['me']['messages']} msgs · {s['claude']['tool_calls']:,} tools · "
        f"${c['api_equivalent_usd']:,.0f} vs {c['plan_label']} "
        f"(saved ${c['saved_usd']:,.0f}, {c['multiple']}x)"
    )
    if s["comparison"] is None:
        print("  comparison: hidden (prior window not provably complete)")
