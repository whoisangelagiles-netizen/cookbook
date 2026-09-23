#!/usr/bin/env python3
"""
shortlinks.py — per-account short links (Dub) for the High Protein House bio
link, so we can see how many people actually tap through to the product.

Why: TikTok reports profile views but not bio-link taps, and Gumroad reports
sales but not clicks. The gap between "saw the post" and "bought" is invisible
without a link that counts taps. One short link per account closes it, and the
existing ?ref=hphNN tag rides along to the destination so Gumroad still
attributes sales per account.

Subcommands
-----------
  create [--dry-run] [--domain dub.sh] [--prefix hph]
      For each account in state/account-voices.json, ensure a short link
      exists pointing at that account's bio_link. Idempotent: an account
      that already has a link in state/shortlinks.json is left alone.
      Tries a readable slug first (hph-macro-architect) and falls back to a
      Dub-generated one if that slug is taken. Prints handle -> short link.

  clicks [--dry-run]
      Read lifetime click totals for every link in ONE GET /links call and
      append today's delta to a per-day series in state/shortlink-clicks.json.
      One call rather than twenty, because Dub rate-limits this plan hard.

  list
      Print what we have on file. No network.

Auth: a Dub API key attached to the cloud environment as an API credential for
api.dub.co, injected by the egress proxy exactly like the OpenAI key (see
routines/cookbook-daily-3x.md). When DUB_TOKEN is unset or holds the
placeholder value "proxy", no Authorization header is sent and the proxy adds
it. Any other value is sent as a Bearer token, for local use. Optional
DUB_WORKSPACE_ID is appended as workspaceId when set. The key is never printed
or written anywhere.

`clicks` never exits non-zero: click data is reporting, and must never
interfere with posting.

API shapes (verified against the live API on 2026-09-23):
  POST /links       {url, domain?, key?} -> {id, domain, key, shortLink, ...}
  GET  /links?pageSize=&page=            -> [ {...link, clicks, leads, sales,
                                              lastClicked}, ... ]
  GET  /links/info?domain=&key=          -> one link, same fields
  GET  /analytics?...                    -> 429 rate_limit_exceeded on this
      plan, persistently, even after 30s backoff. Not used: /links already
      carries the click totals, and the per-day series is derived from daily
      snapshots of those totals.
"""

import argparse
import datetime as dt
import time
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VOICES = os.path.join(ROOT, "state", "account-voices.json")
LINKS = os.path.join(ROOT, "state", "shortlinks.json")
CLICKS = os.path.join(ROOT, "state", "shortlink-clicks.json")
API = "https://api.dub.co"
SKIP = {"@angelagiles29"}
# Dub rate-limits the free tier; 20 analytics calls back-to-back tripped a 429
# on 2026-09-23. Space calls out and honour Retry-After rather than losing the
# whole read.
MIN_GAP = float(os.environ.get("DUB_MIN_GAP_SECONDS", "1.5") or 1.5)
MAX_TRIES = 4
MAX_BACKOFF = 60.0
_last_call = [0.0]


def retry_after_seconds(hdrs, floor):
    """How long to wait after a 429/5xx, in seconds.

    RFC 7231 says Retry-After carries delta-seconds, but Dub sends an epoch
    timestamp in milliseconds - often one that is already in the past. Taking
    that at face value asks time.sleep() for roughly 57,000 years, which
    raises OverflowError and loses the whole read. Treat an epoch-sized value
    as an absolute time, and clamp the result so a bad header cannot park the
    run.
    """
    raw = hdrs.get("Retry-After") or hdrs.get("retry-after") or 0
    try:
        wait = float(raw)
    except (TypeError, ValueError):
        wait = 0.0
    if wait > 1e11:      # epoch milliseconds
        wait = wait / 1000.0 - time.time()
    elif wait > 1e9:     # epoch seconds
        wait = wait - time.time()
    return min(max(wait, floor), MAX_BACKOFF)


def log(msg):
    print(msg, flush=True)


def load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def save_json(path, data):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def headers():
    tok = os.environ.get("DUB_TOKEN", "").strip()
    h = {"Content-Type": "application/json"}
    if tok and tok.lower() not in ("proxy", "placeholder", "injected"):
        h["Authorization"] = f"Bearer {tok}"
    return h


def with_workspace(path):
    ws = os.environ.get("DUB_WORKSPACE_ID", "").strip()
    if not ws:
        return path
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}workspaceId={urllib.parse.quote(ws)}"


def call(method, path, payload=None, timeout=60):
    """Returns (status, parsed_json_or_text). Never raises.

    Paces requests at MIN_GAP apart and retries a 429 (or a 5xx) up to
    MAX_TRIES, honouring Retry-After when the server sends one.
    """
    url = with_workspace(path if path.startswith("http") else API + path)
    data = json.dumps(payload).encode() if payload is not None else None
    for attempt in range(1, MAX_TRIES + 1):
        gap = MIN_GAP - (time.monotonic() - _last_call[0])
        if gap > 0:
            time.sleep(gap)
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in headers().items():
            req.add_header(k, v)
        hdrs = {}
        try:
            with urllib.request.urlopen(req, timeout=timeout,
                                        context=ssl.create_default_context()) as r:
                body, status, hdrs = r.read(), r.status, dict(r.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else b""
            status, hdrs = exc.code, dict(exc.headers or {})
        except Exception as exc:
            _last_call[0] = time.monotonic()
            if attempt < MAX_TRIES:
                time.sleep(3 * attempt)
                continue
            return 0, {"error": f"{exc.__class__.__name__}: {exc}"}
        _last_call[0] = time.monotonic()
        if status in (429, 500, 502, 503, 504) and attempt < MAX_TRIES:
            limit = str(hdrs.get("X-Ratelimit-Limit",
                                 hdrs.get("x-ratelimit-limit", ""))).strip()
            if status == 429 and limit == "0":
                # A limit of 0 is not an exhausted quota, it is no quota at
                # all: this endpoint is not enabled for the key's plan. No
                # amount of pacing or waiting changes that, so do not burn
                # retries on it.
                return status, {"error": {"code": "rate_limit_exceeded",
                                          "message": "endpoint quota is 0 for this plan"},
                                "_quota_zero": True}
            time.sleep(retry_after_seconds(hdrs, 5.0 * attempt))
            continue
        try:
            return status, json.loads(body)
        except Exception:
            return status, {"raw": body[:300].decode("utf-8", "replace")}
    return 429, {"error": "rate limited after retries"}


def explain(status, body):
    if status == 401:
        return ("401 unauthorized — no Dub API key reached the API. Attach one as an "
                "API credential for api.dub.co on the routine environment.")
    if status == 403:
        return ("403 forbidden — the key is valid but lacks scope for this call, or "
                "the workspace is wrong (set DUB_WORKSPACE_ID).")
    if status == 429:
        if isinstance(body, dict) and body.get("_quota_zero"):
            return ("429 rate limited: X-Ratelimit-Limit is 0 for this endpoint, so the "
                    "plan behind this key does not include the analytics API. Pacing "
                    "cannot fix a quota of zero; the plan has to change.")
        return "429 rate limited — wait a minute and re-run; created links are kept."
    if status == 0:
        return f"network error: {body.get('error')}"
    msg = ""
    if isinstance(body, dict):
        err = body.get("error")
        msg = err.get("message") if isinstance(err, dict) else (err or body.get("message") or "")
    return f"HTTP {status} {msg or json.dumps(body)[:200]}"


def accounts():
    v = load_json(VOICES, {})
    acc = v.get("accounts", v)
    out = []
    for handle, cfg in sorted(acc.items()):
        if not handle.startswith("@") or handle in SKIP:
            continue
        if isinstance(cfg, dict) and cfg.get("bio_link"):
            out.append((handle, cfg["bio_link"]))
    return out


def slug_for(handle, prefix):
    base = re.sub(r"[^a-z0-9]+", "-", handle.lstrip("@").lower()).strip("-")
    return f"{prefix}-{base}" if prefix else base


def short_of(body):
    """Pull the short URL out of a create/list response, whatever it is called."""
    if not isinstance(body, dict):
        return None
    for k in ("shortLink", "short_link", "shortUrl", "url_short"):
        if body.get(k):
            return body[k]
    if body.get("domain") and body.get("key"):
        return f"https://{body['domain']}/{body['key']}"
    return None


# ---------------------------------------------------------------------------
def cmd_create(args):
    store = load_json(LINKS, {"schema_version": 1, "provider": "dub", "links": {}})
    links = store.setdefault("links", {})
    store["provider"] = "dub"
    rows = accounts()
    if not rows:
        log("no accounts with a bio_link in state/account-voices.json")
        return 1

    made = skipped = failed = 0
    for handle, long_url in rows:
        if links.get(handle, {}).get("link"):
            skipped += 1
            continue
        want = slug_for(handle, args.prefix)
        if args.dry_run:
            log(f"would create: {handle:20s} {args.domain}/{want}  ->  {long_url}")
            made += 1
            continue

        entry = None
        for key in (want, None):  # preferred slug, then let Dub pick one
            payload = {"url": long_url}
            if args.domain:
                payload["domain"] = args.domain
            if key:
                payload["key"] = key
            status, body = call("POST", "/links", payload)
            link = short_of(body)
            if status in (200, 201) and link:
                entry = {"link": link, "id": body.get("id"),
                         "domain": body.get("domain") or args.domain,
                         "key": body.get("key") or key,
                         "long_url": long_url, "created": dt.date.today().isoformat()}
                break
            if key and status in (409, 422, 400):
                log(f"  {handle}: slug '{key}' unavailable, letting Dub choose")
                continue
            log(f"FAILED {handle}: " + explain(status, body))
            if status in (401, 403):
                # every account would fail the same way — stop rather than
                # printing the same credential error ten times
                log("stopping: fix the credential and re-run (links already "
                    "created are kept and will be skipped)")
                failed += 1
                store["last_updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                save_json(LINKS, store)
                log(f"\ncreated={made} already-had={skipped} failed={failed}")
                return 1
            break

        if entry:
            links[handle] = entry
            made += 1
            log(f"created {handle:20s} {entry['link']}")
            save_json(LINKS, store)  # persist as we go; a 429 mid-run loses nothing
        else:
            failed += 1

    if not args.dry_run:
        store["last_updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        save_json(LINKS, store)
    log(f"\ncreated={made} already-had={skipped} failed={failed}")
    if links and not args.dry_run:
        log("\nBio links to set on TikTok (Website field):")
        for handle, _ in rows:
            e = links.get(handle)
            if e:
                log(f"  {handle:20s} {e['link']}")
    return 1 if failed and not made else 0


def fetch_all_links(page_size=100):
    """Every link in the workspace, in as few calls as possible.

    GET /links returns `clicks` (lifetime total) per link, so one call covers
    all ten accounts. The /analytics endpoint would give a per-day breakdown
    directly, but it answers 429 rate_limit_exceeded on this plan even after
    long waits (probed 2026-09-23), so the per-day series is derived from
    daily snapshots of these totals instead — the same trick GrowthDaily uses
    for follower counts.
    """
    out, page = [], 1
    while True:
        status, body = call("GET", f"/links?pageSize={page_size}&page={page}")
        if status != 200:
            return status, body, out
        batch = body if isinstance(body, list) else (body.get("links") or body.get("data") or [])
        out.extend(x for x in batch if isinstance(x, dict))
        if len(batch) < page_size or page >= 10:
            return status, body, out
        page += 1


def cmd_clicks(args):
    store = load_json(LINKS, {"links": {}})
    links = store.get("links", {})
    if not links:
        log("no links on file yet — run: python3 scripts/shortlinks.py create")
        return 0

    if args.dry_run:
        log(f"would read totals for {len(links)} link(s) in one GET /links call")
        return 0

    status, body, remote = fetch_all_links()
    if status != 200:
        log("could not read links: " + explain(status, body))
        return 0  # reporting only — never disturb posting
    by_id = {x.get("id"): x for x in remote if x.get("id")}
    by_key = {(x.get("domain"), x.get("key")): x for x in remote if x.get("key")}

    out = load_json(CLICKS, {"schema_version": 2, "provider": "dub", "accounts": {}})
    per = out.setdefault("accounts", {})
    today = dt.date.today().isoformat()
    seen = missing = 0
    total_today = 0

    for handle, entry in sorted(links.items()):
        remote_link = by_id.get(entry.get("id")) or by_key.get((entry.get("domain"), entry.get("key")))
        if not remote_link:
            log(f"{handle}: link not found in the workspace listing")
            missing += 1
            continue
        total = int(remote_link.get("clicks") or 0)
        rec = per.setdefault(handle, {"link": entry.get("link"), "id": entry.get("id"),
                                      "clicks_total": 0, "daily": {}})
        prev = rec.get("clicks_total", 0)
        # First sight of a link records the total but no delta — we cannot know
        # which day those clicks happened on.
        if rec.get("daily") or prev:
            rec.setdefault("daily", {})[today] = max(0, total - prev)
        rec["clicks_total"] = total
        rec["link"] = entry.get("link")
        rec["id"] = entry.get("id")
        rec["leads"] = int(remote_link.get("leads") or 0)
        rec["sales"] = int(remote_link.get("sales") or 0)
        rec["last_clicked"] = remote_link.get("lastClicked")
        rec["fetched"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        seen += 1
        total_today += rec.get("daily", {}).get(today, 0)
        delta = rec.get("daily", {}).get(today)
        shown = f"+{delta}" if delta is not None else "  -"
        log(f"{handle:20s} {str(entry.get('link')):34s} {total:5d} total {shown:>5s} today")

    out["last_updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    save_json(CLICKS, out)
    log(f"\n{seen} link(s) read, {missing} missing. "
        f"Clicks today across accounts: {total_today}. -> {CLICKS}")
    return 0


def cmd_list(args):
    links = load_json(LINKS, {"links": {}}).get("links", {})
    if not links:
        log("no links on file")
        return 0
    for handle, e in sorted(links.items()):
        log(f"{handle:20s} {str(e.get('link')):30s} -> {e.get('long_url')}")
    clicks = load_json(CLICKS, {}).get("accounts", {})
    if clicks:
        log("")
        for handle, c in sorted(clicks.items()):
            daily = c.get("daily") or {}
            recent = sorted(daily.items())[-1] if daily else None
            tail = f", {recent[1]} on {recent[0]}" if recent else ""
            log(f"{handle:20s} {c.get('clicks_total', 0)} clicks total{tail}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="per-account Dub short links")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--domain", default="dub.sh", help="short domain (default dub.sh)")
    c.add_argument("--prefix", default="hph", help="slug prefix, '' to disable")
    k = sub.add_parser("clicks")
    k.add_argument("--dry-run", action="store_true")
    sub.add_parser("list")
    args = ap.parse_args()
    fn = {"create": cmd_create, "clicks": cmd_clicks, "list": cmd_list}[args.cmd]
    try:
        sys.exit(fn(args) or 0)
    except Exception as exc:
        log(f"shortlinks failed: {exc.__class__.__name__}: {exc}")
        sys.exit(0 if args.cmd == "clicks" else 1)


if __name__ == "__main__":
    main()
