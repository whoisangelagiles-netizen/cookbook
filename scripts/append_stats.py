#!/usr/bin/env python3
"""
Collect daily TikTok stats for the High Protein House accounts and write them
into the HPH Operations Tracker (GrowthDaily + PostLog).

Usage:
    python scripts/append_stats.py            # collect and write
    python scripts/append_stats.py --dry-run  # collect and print, write nothing

Why scraping and not an API: Blotato does not collect TikTok analytics (its
post-analytics endpoint covers Twitter, Instagram, Facebook, Threads and
Bluesky only), and TikTok's Display API needs its own app approval. The public
embed endpoints expose a JSON state blob that carries what we need.

FAILURE ISOLATION IS NON-NEGOTIABLE. This script runs after the posting run.
Collection, auth and each tab's write are wrapped separately; every failure is
logged to state/automation-log.md with a STATS prefix and swallowed. The
process always exits 0. Nothing here may ever interrupt a posting run.
"""

import argparse
import base64
import concurrent.futures as futures
import datetime as dt
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

SHEET_ID = os.environ.get("HPH_SHEET_ID", "1feoqMkpPLQknuWWZxvkalvBHdoboCQDGftwC-7JRN34")
SA_KEY_PATH = os.environ.get("HPH_SA_KEY", "secrets/sa-key.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

GROWTH_TAB = "GrowthDaily"
POSTLOG_TAB = "PostLog"
HEADER_ROW = 4
DATA_START_ROW = 5

ET = ZoneInfo("America/New_York")
LOG_PATH = "state/automation-log.md"
ROTATION_PATH = "state/recipe-rotation-log.json"

# Tracker handle -> actual TikTok username. These differ; do not assume they match.
ACCOUNTS = {
    "@cleanfuel.kitchen": "getfuel.kitchen",
    "@coach.macro": "macro.coaching",
    "@fuel.your.gains": "fuel.your.gains",
    "@gymfood.simple": "gymfoodsimple",
    "@macro.architect": "macro.architect",
    "@postworkout.plate": "postworkout.plate",
    "@prep.with.alex": "prep.with.alex",
    "@protein.lab.eats": "proteinlabseat",
    "@the.lean.cook": "theleancook5",
    "@under10.protein": "under10.protein",
}
# @angelagiles29 is deliberately absent and must never be collected.

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
STATE_RE = re.compile(
    r'<script[^>]*id="__FRONTITY_CONNECT_STATE__"[^>]*>(.*?)</script>', re.S)
MAX_WORKERS = 4
RETRIES = 3
TIMEOUT = 30


def log(msg):
    """Append a STATS line to the automation log. Never raises."""
    line = "- [%s] STATS: %s\n" % (
        dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), msg)
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write(line)
    except Exception:
        pass
    print(line.rstrip(), file=sys.stderr)


# ---------------------------------------------------------------- fetching

def _get(url):
    """GET with a desktop UA, gzip handling and backoff. Returns text or None."""
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept-Encoding": "gzip",
                              "Accept-Language": "en-US,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "replace")
        except Exception as exc:
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** attempt)
    log("fetch failed after %d tries: %s (%s)" % (RETRIES, url, last))
    return None


def _state(html):
    m = STATE_RE.search(html or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def fetch_profile(handle, username):
    """Return dict with followers, hearts and recent posts, or None."""
    html = _get("https://www.tiktok.com/embed/@%s" % username)
    st = _state(html)
    if not st:
        log("no state blob for profile @%s" % username)
        return None
    try:
        node = st["source"]["data"]["/embed/@%s" % username]
        info = node.get("userInfo") or {}
        vids = node.get("videoList") or []
        return {
            "handle": handle,
            "username": username,
            "followers": int(info.get("followerCount") or 0),
            "hearts": int(info.get("heartCount") or 0),
            "posts": [{"id": str(v.get("id")), "desc": v.get("desc") or "",
                       "playCount": int(v.get("playCount") or 0)}
                      for v in vids if v.get("id")],
        }
    except Exception as exc:
        log("profile parse failed @%s: %s" % (username, exc))
        return None


def fetch_post(video_id):
    """Return dict of engagement + createTime for one post, or None."""
    html = _get("https://www.tiktok.com/embed/v2/%s" % video_id)
    st = _state(html)
    if not st:
        return None
    try:
        it = st["source"]["data"]["/embed/v2/%s" % video_id]["videoData"]["itemInfos"]
        return {
            "id": str(video_id),
            "createTime": int(it.get("createTime") or 0),
            "views": int(it.get("playCount") or 0),
            "likes": int(it.get("diggCount") or 0),
            "comments": int(it.get("commentCount") or 0),
            "shares": int(it.get("shareCount") or 0),
        }
    except Exception as exc:
        log("post parse failed %s: %s" % (video_id, exc))
        return None


# ---------------------------------------------------------------- matching

def load_recipes():
    try:
        with open(ROTATION_PATH) as fh:
            return sorted(json.load(fh)["recipes"].keys(), key=len, reverse=True)
    except Exception as exc:
        log("could not load recipes: %s" % exc)
        return []


def match_recipe(desc, recipes):
    """Longest name first so specific titles beat generic substrings."""
    d = (desc or "").lower()
    for name in recipes:
        if name.lower() in d:
            return name
    return ""


def slot_for(hour):
    if hour < 10:
        return "Breakfast"
    if hour < 15:
        return "Lunch"
    return "Dinner"


# ---------------------------------------------------------------- collect

def collect():
    recipes = load_recipes()
    profiles, posts = [], []

    with futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for res in ex.map(lambda kv: fetch_profile(*kv), list(ACCOUNTS.items())):
            if res:
                profiles.append(res)

    wanted = []
    for p in profiles:
        for post in p["posts"]:
            wanted.append((p["handle"], post))

    detail = {}
    with futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_post, pid): pid
                for pid in {pp["id"] for _, pp in wanted}}
        for f in futures.as_completed(futs):
            r = f.result()
            if r:
                detail[r["id"]] = r

    for handle, stub in wanted:
        d = detail.get(stub["id"])
        if not d:
            continue
        when = dt.datetime.fromtimestamp(d["createTime"], dt.timezone.utc).astimezone(ET)
        posts.append({
            "post_id": d["id"],
            "account": handle,
            "date": when.strftime("%Y-%m-%d"),
            "sched_time": when.strftime("%H:%M"),
            "slot": slot_for(when.hour),
            "recipe": match_recipe(stub["desc"], recipes),
            "desc": stub["desc"],
            "views": d["views"],
            "likes": d["likes"],
            "comments": d["comments"],
            "shares": d["shares"],
            "created_utc": d["createTime"],
        })

    return {
        "collected_at": dt.datetime.now(ET).strftime("%Y-%m-%d %H:%M %Z"),
        "today_et": dt.datetime.now(ET).strftime("%Y-%m-%d"),
        "profiles": [{k: v for k, v in p.items() if k != "posts"} for p in profiles],
        "posts": posts,
    }


# ---------------------------------------------------------------- sheets

def open_sheet():
    import gspread
    from google.oauth2.service_account import Credentials
    raw = open(SA_KEY_PATH).read().strip()
    # Accept raw JSON or base64-encoded JSON; the format is not knowable from
    # the filename and guessing wrong silently broke this on 2026-08-07.
    info = json.loads(raw) if raw.startswith("{") else json.loads(base64.b64decode(raw))
    return gspread.authorize(
        Credentials.from_service_account_info(info, scopes=SCOPES)
    ).open_by_key(SHEET_ID)


def gained_formula(col, row):
    """Delta against this account's most recent previous entry.

    The spec called for LOOKUP(2,1/(cond),result). That idiom returns #N/A in
    this spreadsheet even when COUNTIF confirms a prior row exists (verified
    2026-08-24). INDEX(FILTER(...), COUNTIF(...)) is equivalent — FILTER keeps
    the matching rows in order, COUNTIF indexes the last one — and evaluates
    correctly here.
    """
    p = row - 1
    return ('=IF($B{r}="","",IF(COUNTIF($B${h}:$B{p},$B{r})=0,"",'
            '{c}{r}-INDEX(FILTER({c}${h}:{c}{p},$B${h}:$B{p}=$B{r}),'
            'COUNTIF($B${h}:$B{p},$B{r}))))'
            ).format(r=row, p=p, h=HEADER_ROW, c=col)


def write_growth(sh, payload):
    ws = sh.worksheet(GROWTH_TAB)
    existing = ws.get_values("A%d:B" % DATA_START_ROW)
    seen = {(r[0].strip(), r[1].strip())
            for r in existing if len(r) >= 2 and r[0] and r[1]}
    first_free = DATA_START_ROW + len(existing)

    today = payload["today_et"]
    rows, row_no = [], first_free
    for p in sorted(payload["profiles"], key=lambda x: x["handle"]):
        if (today, p["handle"]) in seen:
            continue
        rows.append([today, p["handle"], p["followers"], p["hearts"],
                     gained_formula("C", row_no), gained_formula("D", row_no), "auto"])
        row_no += 1

    if not rows:
        log("GrowthDaily: today's snapshot already present, appended 0 rows")
        return 0

    ws.update(values=rows,
              range_name="A%d:G%d" % (first_free, first_free + len(rows) - 1),
              value_input_option="USER_ENTERED")
    log("GrowthDaily: appended %d row(s) at %d" % (len(rows), first_free))
    return len(rows)


def write_postlog(sh, payload):
    ws = sh.worksheet(POSTLOG_TAB)
    grid = ws.get_values("A%d:N" % DATA_START_ROW)
    id_to_row, last_row = {}, DATA_START_ROW - 1
    for i, r in enumerate(grid):
        row_no = DATA_START_ROW + i
        if any(c.strip() for c in r):
            last_row = row_no
        pid = r[12].strip() if len(r) > 12 else ""
        if pid:
            id_to_row[pid] = (row_no, r[13].strip() if len(r) > 13 else "")

    today = payload["today_et"]
    updates, appends, frozen = [], [], 0

    for p in sorted(payload["posts"], key=lambda x: x["created_utc"]):
        hit = id_to_row.get(p["post_id"])
        age_h = (time.time() - p["created_utc"]) / 3600.0
        if hit:
            row_no, stats_as_of = hit
            # Freeze: "Views (24h)" must stop moving once a genuine 24h number
            # has been captured, i.e. stamped at least a day after the post.
            if age_h >= 24 and stats_as_of:
                try:
                    if (dt.date.fromisoformat(stats_as_of)
                            - dt.date.fromisoformat(p["date"])).days >= 1:
                        frozen += 1
                        continue
                except Exception:
                    pass
            updates.append((row_no, p))
        else:
            appends.append(p)

    for row_no, p in updates:
        ws.update(values=[[p["views"], p["likes"], p["comments"], p["shares"]]],
                  range_name="G%d:J%d" % (row_no, row_no),
                  value_input_option="USER_ENTERED")
        ws.update(values=[[today]], range_name="N%d" % row_no,
                  value_input_option="USER_ENTERED")

    if appends:
        start = last_row + 1
        block = []
        for p in appends:
            block.append([p["date"], p["recipe"], p["slot"], p["account"],
                          p["sched_time"], "Y", p["views"], p["likes"],
                          p["comments"], p["shares"], "", "auto",
                          p["post_id"], today])
        ws.update(values=block,
                  range_name="A%d:N%d" % (start, start + len(block) - 1),
                  value_input_option="USER_ENTERED")

    log("PostLog: %d updated, %d appended, %d frozen (skipped)"
        % (len(updates), len(appends), frozen))
    return len(updates), len(appends), frozen


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="collect and print the payload as JSON; write nothing")
    args = ap.parse_args()

    try:
        payload = collect()
    except Exception as exc:
        log("collection failed, nothing written: %s" % exc)
        return

    n_prof, n_post = len(payload["profiles"]), len(payload["posts"])
    unmatched = [p["post_id"] for p in payload["posts"] if not p["recipe"]]

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        print("\n--- summary ---", file=sys.stderr)
        print("profiles: %d / %d" % (n_prof, len(ACCOUNTS)), file=sys.stderr)
        print("posts: %d" % n_post, file=sys.stderr)
        print("unmatched recipes: %d %s"
              % (len(unmatched), unmatched[:10]), file=sys.stderr)
        return

    if n_prof == 0 and n_post == 0:
        log("collected nothing (0 profiles, 0 posts) — skipping all writes")
        return
    if unmatched:
        log("%d post(s) had no recipe match: %s" % (len(unmatched), unmatched[:10]))

    try:
        sh = open_sheet()
    except Exception as exc:
        log("sheet auth/open failed, nothing written: %s" % exc)
        return

    try:
        write_growth(sh, payload)
    except Exception as exc:
        log("GrowthDaily write failed: %s" % exc)

    try:
        write_postlog(sh, payload)
    except Exception as exc:
        log("PostLog write failed: %s" % exc)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:          # last-resort net; never propagate
        log("unhandled: %s" % exc)
    sys.exit(0)                        # always 0 — never fail the posting run
