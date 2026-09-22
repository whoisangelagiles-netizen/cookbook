#!/usr/bin/env python3
"""
shortlinks.py — per-account short links for the High Protein House bio link,
so we can see how many people actually tap through to the product.

Why: TikTok reports profile views but not bio-link taps, and Gumroad reports
sales but not clicks. The gap between "saw the post" and "bought" is invisible
without a link that counts taps. One short link per account closes it, and the
existing ?ref=hphNN tag rides along to the destination so Gumroad still
attributes sales per account.

Subcommands
-----------
  create [--dry-run]
      For each account in state/account-voices.json, ensure a short link
      exists pointing at that account's bio_link. Idempotent: an account
      that already has a link in state/shortlinks.json is left alone.
      Prints a table of handle -> short link for the bio update.

  clicks [--days N] [--dry-run]
      Fetch click counts for every known link (default: last 30 days,
      per-day breakdown) and write them to state/shortlink-clicks.json.
      Prints a per-account summary.

  list
      Print what we have on file. No network.

Auth: a Bitly API token attached to the cloud environment as an API credential
for api-ssl.bitly.com, injected by the egress proxy exactly like the OpenAI
key (see routines/cookbook-daily-3x.md). When BITLY_TOKEN is unset or holds
the placeholder value "proxy", no Authorization header is sent and the proxy
adds it. Any other value is sent as a Bearer token, for local use. The token
is never printed or written anywhere.

Never exits non-zero on a network/API failure during `clicks` — click data is
reporting, and must never interfere with posting.

API shapes used (Bitly v4 — verify against the live response on first run):
  POST /v4/shorten                      {long_url, group_guid?}  -> {id, link}
  GET  /v4/groups                       -> {groups: [{guid, name}]}
  GET  /v4/bitlinks/{id}/clicks/summary?unit=day&units=N -> {total_clicks}
  GET  /v4/bitlinks/{id}/clicks?unit=day&units=N         -> {link_clicks: [...]}
"""

import argparse
import datetime as dt
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VOICES = os.path.join(ROOT, "state", "account-voices.json")
LINKS = os.path.join(ROOT, "state", "shortlinks.json")
CLICKS = os.path.join(ROOT, "state", "shortlink-clicks.json")
API = "https://api-ssl.bitly.com"
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
    tok = os.environ.get("BITLY_TOKEN", "").strip()
    h = {"Content-Type": "application/json"}
    if tok and tok.lower() not in ("proxy", "placeholder", "injected"):
        h["Authorization"] = f"Bearer {tok}"
    return h


def call(method, path, payload=None, timeout=60):
    """Returns (status, parsed_json_or_text). Never raises."""
    url = path if path.startswith("http") else API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers().items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as r:
            body = r.read()
            status = r.status
    except urllib.error.HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        status = exc.code
    except Exception as exc:
        return 0, {"error": f"{exc.__class__.__name__}: {exc}"}
    try:
        return status, json.loads(body)
    except Exception:
        return status, {"raw": body[:300].decode("utf-8", "replace")}


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


def explain(status, body):
    if status == 401:
        return ("401 UNAUTHORIZED — no Bitly token reached the API. Attach one as "
                "an API credential for api-ssl.bitly.com on the routine environment.")
    if status == 403:
        return ("403 FORBIDDEN — the token is valid but the plan or scope does not "
                "allow this call (Bitly restricts API access on some free plans).")
    if status == 0:
        return f"network error: {body.get('error')}"
    return f"HTTP {status} {json.dumps(body)[:300]}"


# ---------------------------------------------------------------------------
def cmd_create(args):
    store = load_json(LINKS, {"schema_version": 1, "provider": "bitly", "links": {}})
    links = store.setdefault("links", {})
    rows = accounts()
    if not rows:
        log("no accounts with a bio_link in state/account-voices.json")
        return 1

    group = None
    if not args.dry_run:
        status, body = call("GET", "/v4/groups")
        if status != 200:
            log("could not read Bitly groups: " + explain(status, body))
            return 1
        groups = body.get("groups") or []
        if groups:
            group = groups[0].get("guid")
            log(f"using Bitly group {groups[0].get('name')} ({group})")

    made = skipped = failed = 0
    for handle, long_url in rows:
        have = links.get(handle)
        if have and have.get("link"):
            skipped += 1
            continue
        if args.dry_run:
            log(f"would create: {handle:20s} -> {long_url}")
            made += 1
            continue
        payload = {"long_url": long_url}
        if group:
            payload["group_guid"] = group
        status, body = call("POST", "/v4/shorten", payload)
        if status not in (200, 201) or not body.get("link"):
            log(f"FAILED {handle}: " + explain(status, body))
            failed += 1
            continue
        links[handle] = {
            "link": body["link"],
            "id": body.get("id") or body["link"].replace("https://", ""),
            "long_url": long_url,
            "created": dt.date.today().isoformat(),
        }
        made += 1
        log(f"created {handle:20s} {body['link']}")

    if not args.dry_run:
        store["last_updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        save_json(LINKS, store)
    log(f"\ncreated={made} already-had={skipped} failed={failed}")
    if made or skipped:
        log("\nBio links to set on TikTok (Website field):")
        for handle, _ in rows:
            entry = links.get(handle)
            if entry:
                log(f"  {handle:20s} {entry['link']}")
    return 1 if failed and not made else 0


def cmd_clicks(args):
    store = load_json(LINKS, {"links": {}})
    links = store.get("links", {})
    if not links:
        log("no links on file yet — run: python3 scripts/shortlinks.py create")
        return 0

    out = load_json(CLICKS, {"schema_version": 1, "accounts": {}})
    per = out.setdefault("accounts", {})
    ok = bad = 0
    for handle, entry in sorted(links.items()):
        bid = entry.get("id")
        if not bid:
            continue
        if args.dry_run:
            log(f"would fetch clicks for {handle} ({bid})")
            continue
        status, body = call("GET", f"/v4/bitlinks/{bid}/clicks/summary"
                                   f"?unit=day&units={args.days}")
        if status != 200:
            log(f"{handle}: " + explain(status, body))
            bad += 1
            continue
        total = body.get("total_clicks", 0)
        s2, daily = call("GET", f"/v4/bitlinks/{bid}/clicks?unit=day&units={args.days}")
        series = {}
        if s2 == 200:
            for pt in daily.get("link_clicks", []) or []:
                day = (pt.get("date") or "")[:10]
                if day:
                    series[day] = pt.get("clicks", 0)
        per[handle] = {"link": entry.get("link"), "id": bid,
                       f"clicks_{args.days}d": total, "daily": series,
                       "fetched": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        ok += 1
        log(f"{handle:20s} {entry.get('link'):28s} {total:5d} clicks / {args.days}d")

    if not args.dry_run and ok:
        out["last_updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        save_json(CLICKS, out)
        total = sum(v.get(f"clicks_{args.days}d", 0) for v in per.values())
        log(f"\ntotal across accounts: {total} clicks in {args.days} days "
            f"({ok} links read, {bad} failed) -> {CLICKS}")
    return 0


def cmd_list(args):
    store = load_json(LINKS, {"links": {}})
    links = store.get("links", {})
    if not links:
        log("no links on file")
        return 0
    for handle, e in sorted(links.items()):
        log(f"{handle:20s} {e.get('link'):30s} -> {e.get('long_url')}")
    clicks = load_json(CLICKS, {}).get("accounts", {})
    if clicks:
        log("")
        for handle, c in sorted(clicks.items()):
            key = next((k for k in c if k.startswith("clicks_")), None)
            log(f"{handle:20s} {c.get(key, 0)} clicks ({key})")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("--dry-run", action="store_true")
    k = sub.add_parser("clicks"); k.add_argument("--days", type=int, default=30)
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
