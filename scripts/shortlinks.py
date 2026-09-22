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

  clicks [--days N] [--dry-run]
      Fetch click counts for every known link (default: last 30 days, with a
      per-day series) into state/shortlink-clicks.json, and print a summary.

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

API shapes (Dub v1 — the response parsers below tolerate several shapes
because these have moved between versions; verify on the first real run):
  POST /links      {url, domain?, key?}  -> {id, domain, key, shortLink, ...}
  GET  /links?domain=&search=            -> [ {...link}, ... ]
  GET  /analytics?event=clicks&groupBy=count&linkId=&interval=30d
                                         -> {clicks: N}
  GET  /analytics?event=clicks&groupBy=timeseries&linkId=&interval=30d
                                         -> [ {start, clicks}, ... ]
"""

import argparse
import datetime as dt
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
    """Returns (status, parsed_json_or_text). Never raises."""
    url = with_workspace(path if path.startswith("http") else API + path)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers().items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as r:
            body, status = r.read(), r.status
    except urllib.error.HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        status = exc.code
    except Exception as exc:
        return 0, {"error": f"{exc.__class__.__name__}: {exc}"}
    try:
        return status, json.loads(body)
    except Exception:
        return status, {"raw": body[:300].decode("utf-8", "replace")}


def explain(status, body):
    if status == 401:
        return ("401 unauthorized — no Dub API key reached the API. Attach one as an "
                "API credential for api.dub.co on the routine environment.")
    if status == 403:
        return ("403 forbidden — the key is valid but lacks scope for this call, or "
                "the workspace is wrong (set DUB_WORKSPACE_ID).")
    if status == 429:
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


def parse_clicks(body):
    """Return (total, {day: clicks}) from whichever analytics shape came back."""
    total, series = 0, {}
    if isinstance(body, dict):
        for k in ("clicks", "count", "total"):
            if isinstance(body.get(k), (int, float)):
                total = int(body[k])
                break
    elif isinstance(body, list):
        for pt in body:
            if not isinstance(pt, dict):
                continue
            n = pt.get("clicks", pt.get("count", 0)) or 0
            total += int(n)
            day = str(pt.get("start") or pt.get("date") or "")[:10]
            if day:
                series[day] = series.get(day, 0) + int(n)
    return total, series


def cmd_clicks(args):
    links = load_json(LINKS, {"links": {}}).get("links", {})
    if not links:
        log("no links on file yet — run: python3 scripts/shortlinks.py create")
        return 0

    out = load_json(CLICKS, {"schema_version": 1, "provider": "dub", "accounts": {}})
    per = out.setdefault("accounts", {})
    interval = f"{args.days}d"
    ok = bad = 0
    for handle, entry in sorted(links.items()):
        lid = entry.get("id")
        q = f"linkId={urllib.parse.quote(str(lid))}" if lid else \
            f"domain={urllib.parse.quote(entry.get('domain',''))}&key={urllib.parse.quote(entry.get('key',''))}"
        if args.dry_run:
            log(f"would fetch clicks for {handle} ({lid or entry.get('key')})")
            continue

        status, body = call("GET", f"/analytics?event=clicks&groupBy=count&{q}&interval={interval}")
        if status != 200:
            log(f"{handle}: " + explain(status, body))
            bad += 1
            continue
        total, _ = parse_clicks(body)
        s2, ts = call("GET", f"/analytics?event=clicks&groupBy=timeseries&{q}&interval={interval}")
        series = parse_clicks(ts)[1] if s2 == 200 else {}
        per[handle] = {"link": entry.get("link"), "id": lid,
                       f"clicks_{args.days}d": total, "daily": series,
                       "fetched": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        ok += 1
        log(f"{handle:20s} {str(entry.get('link')):30s} {total:5d} clicks / {args.days}d")

    if not args.dry_run and ok:
        out["last_updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        save_json(CLICKS, out)
        total = sum(v.get(f"clicks_{args.days}d", 0) for v in per.values())
        log(f"\ntotal across accounts: {total} clicks in {args.days} days "
            f"({ok} read, {bad} failed) -> {CLICKS}")
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
            key = next((k for k in c if k.startswith("clicks_")), "")
            log(f"{handle:20s} {c.get(key, 0)} clicks ({key})")
    return 0


def main():
    ap = argparse.ArgumentParser(description="per-account Dub short links")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--domain", default="dub.sh", help="short domain (default dub.sh)")
    c.add_argument("--prefix", default="hph", help="slug prefix, '' to disable")
    k = sub.add_parser("clicks")
    k.add_argument("--days", type=int, default=30)
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
