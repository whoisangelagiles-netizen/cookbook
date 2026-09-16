#!/usr/bin/env python3
"""
experiment_report.py — read-out for the reach experiments started 2026-09-17.

Joins state/experiments-log.jsonl (what each post was: music on/off, hook
style A/B) with live TikTok stats (scripts/append_stats.py --dry-run) on
(date, slot, account), keeps posts at least 24 hours old, and prints views
and likes by group.

Usage:
    python3 scripts/experiment_report.py            # live scrape, ~1-2 min
    python3 scripts/experiment_report.py stats.json # reuse a saved dry-run

Read-only: touches no state, writes nothing, always exits 0.
"""

import json
import os
import statistics as st
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOG = os.path.join(ROOT, "state", "experiments-log.jsonl")


def load_log():
    rows = []
    if not os.path.exists(LOG):
        return rows
    for line in open(LOG):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def live_stats(path=None):
    if path:
        raw = open(path).read()
    else:
        out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "append_stats.py"),
                              "--dry-run"], capture_output=True, text=True, timeout=600)
        raw = out.stdout
    try:
        return json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except Exception as exc:
        print(f"could not parse stats output: {exc}")
        return {"posts": []}


def summarize(label, posts):
    if not posts:
        print(f"  {label:<14} n=0")
        return
    v = [p["views"] for p in posts]
    l = [p["likes"] for p in posts]
    sh = sum(p.get("shares", 0) for p in posts)
    c = sum(p.get("comments", 0) for p in posts)
    print(f"  {label:<14} n={len(posts):3d}  views median={int(st.median(v)):5d} mean={int(st.mean(v)):5d} "
          f"max={max(v):5d}  >1.5k: {100 * sum(x > 1500 for x in v) / len(v):4.0f}%  "
          f"likes/post={st.mean(l):5.1f}  shares={sh:3d} comments={c:3d}")


def main():
    log = load_log()
    if not log:
        print("no experiment rows yet in state/experiments-log.jsonl")
        return
    stats = live_stats(sys.argv[1] if len(sys.argv) > 1 else None)
    now = time.time()
    by_key = {}
    for p in stats.get("posts", []):
        if now - p.get("created_utc", now) < 86400:
            continue  # need a genuine 24h read
        by_key[(p["date"], p["slot"], p["account"].lower())] = p

    joined = []
    for r in log:
        p = by_key.get((r["date"], r["slot"], r["account"].lower()))
        if p:
            q = dict(p)
            q["music"] = bool(r.get("music"))
            q["hook_style"] = r.get("hook_style")
            joined.append(q)

    print(f"experiment posts logged: {len(log)}   matched with 24h+ stats: {len(joined)}")
    if not joined:
        print("nothing old enough to read yet")
        return

    print("\nMUSIC A/B (account-level groups)")
    summarize("music on", [p for p in joined if p["music"]])
    summarize("control", [p for p in joined if not p["music"]])

    print("\nHOOK A/B (per firing, all accounts)")
    summarize("A numbers", [p for p in joined if p["hook_style"] == "A"])
    summarize("B comparison", [p for p in joined if p["hook_style"] == "B"])

    print("\nCELLS")
    for m in (True, False):
        for h in ("A", "B"):
            summarize(f"{'music' if m else 'silent'}/{h}",
                      [p for p in joined if p["music"] == m and p["hook_style"] == h])

    print("\nPER ACCOUNT (experiment posts only)")
    for a in sorted({p["account"] for p in joined}):
        summarize(a, [p for p in joined if p["account"] == a])

    print("\nBASELINE (pre-experiment posts in the same scrape, 24h+)")
    keys = {(r["date"], r["slot"], r["account"].lower()) for r in log}
    base = [p for k, p in by_key.items() if k not in keys]
    summarize("baseline", base)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"report failed: {exc}")
