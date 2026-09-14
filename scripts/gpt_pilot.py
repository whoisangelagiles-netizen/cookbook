#!/usr/bin/env python3
"""
gpt_pilot.py — external slide generation for High Protein House carousels.

Generates 5 slides per post with OpenAI Images (gpt-image-1-mini) + a Pillow
text overlay, then hands the finished PNGs to Blotato as own media (which
schedules at zero credit cost). Started 2026-09-10 as a one-account pilot on
@postworkout.plate; from 2026-09-14 it is the default path for all accounts.

Subcommands
-----------
  render-batch <plan.json>
      plan.json:
        {"date": "2026-09-14",
         "rows": [{"account": "@handle", "recipe": "...",
                   "visual_style_prompt": "...",
                   "slides": [{"image": "...", "text": "..."}, x5]}, ...]}
      For every (row, slide): reuse a cached media URL that still resolves
      (state/media-cache.json, keyed recipe|account|slide_index); otherwise
      generate the background with OpenAI and composite the text locally.
      Backgrounds are fetched concurrently (GPT_PILOT_WORKERS, default 4).
      Rows are isolated: one row failing does not stop the others.
      Writes pilot_manifest.json and prints one GPT-ROW line per row plus a
      final GPT-PILOT-RENDER summary. The manifest's "needs_upload" list
      names every slide that needs a presigned URL, with a suggested filename.

  upload-batch <pilot_manifest.json> <uploads.json>
      uploads.json is written by the routine after calling
      blotato_create_presigned_upload_url for each needs_upload entry:
        [{"account": "@handle", "slide_index": 1,
          "presignedUrl": "...", "publicUrl": "..."}, ...]
      PUTs each PNG, verifies the public URL serves an image, records it in
      the cache, writes pilot_media.json with a 5-URL mediaUrls array per row
      and prints one GPT-MEDIA line per row plus a GPT-PILOT-UPLOAD summary.
      A row whose upload fails is marked failed; the others still complete.

  render <input.json> / upload <manifest> <uploads.json>
      Single-row forms of the above (input.json = one row + "date"). Kept
      for manual use; uploads.json entries may omit "account".

  status
      Print cache size and the spend ledger.

Exit codes: 0 = the manifest / media file was written (check per-row
"status" — "failed" rows must fall back to Blotato generation).
1 = nothing usable was produced (bad input, Pillow missing, ...): the routine
must fall back to Blotato for every row. The script never schedules posts,
never touches the recipe rotation, and never deletes anything.

Auth: the OpenAI key is attached to the cloud environment as an API credential
that the egress proxy injects for api.openai.com. When OPENAI_API_KEY is unset
or holds the placeholder value "proxy", the script sends NO Authorization
header and lets the proxy add it (verified 2026-09-09; the proxy also overrides
a client-supplied header). Any other value is sent as a Bearer token, for
local use. The key is never printed or written anywhere.

Environment knobs (all optional):
  GPT_PILOT_MODEL     default gpt-image-1-mini
  GPT_PILOT_QUALITY   low | medium | high   (default medium)
  GPT_PILOT_WORKERS   concurrent OpenAI requests (default 4)
  GPT_PILOT_RATES     "text_in,image_in,image_out" USD per 1M tokens, used to
                      turn the API's usage object into a cost figure.
                      Default 2.00,2.50,8.00 — VERIFY at openai.com/api/pricing
                      and override here if it differs.
  GPT_PILOT_FAKE=1    skip OpenAI, paint a gradient background (offline tests)
"""

import base64
import concurrent.futures as futures
import datetime as dt
import hashlib
import io
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except ImportError:  # pragma: no cover
    print("GPT-PILOT-ERROR: Pillow is not installed (pip install pillow)")
    sys.exit(1)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CACHE_PATH = os.path.join(ROOT, "state", "media-cache.json")
SPEND_PATH = os.path.join(ROOT, "state", "gpt-pilot-spend.json")
OUT_ROOT = os.path.join(ROOT, "out", "gpt-pilot")
FONT_DIR = os.path.join(ROOT, "assets", "fonts")
MANIFEST_DEFAULT = os.path.join(ROOT, "pilot_manifest.json")
MEDIA_DEFAULT = os.path.join(ROOT, "pilot_media.json")

MODEL = os.environ.get("GPT_PILOT_MODEL", "gpt-image-1-mini")
QUALITY = os.environ.get("GPT_PILOT_QUALITY", "medium")
WORKERS = max(1, int(os.environ.get("GPT_PILOT_WORKERS", "4") or 4))
SIZE = "1024x1536"          # closest OpenAI size to 9:16
W, H = 1024, 1536
N_SLIDES = 5
OPENAI_URL = "https://api.openai.com/v1/images/generations"

# Fallback per-image estimates when the API returns no usage object.
FALLBACK_COST = {"low": 0.005, "medium": 0.013, "high": 0.04}

# TikTok UI safe area, scaled from 1080x1920 to 1024x1536:
#   top ~150px (status/search), right ~130px (like/comment rail),
#   bottom ~290px (caption + nav). Everything textual stays inside this box.
SAFE_LEFT, SAFE_RIGHT, SAFE_TOP, SAFE_BOTTOM = 64, W - 140, 150, H - 290

NO_TEXT_SUFFIX = (
    " No text, no letters, no numbers, no logos, no watermarks, no captions, "
    "no people, no hands. Photorealistic food photography."
)


# ----------------------------------------------------------------------------
# small utils
# ----------------------------------------------------------------------------
_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def fail(msg):
    log(f"GPT-PILOT-ERROR: {msg}")
    sys.exit(1)


def load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except Exception as exc:
        fail(f"cannot parse {path}: {exc}")


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def cache_key(recipe, account, idx):
    return f"{recipe.strip()}|{account.strip().lower()}|{int(idx)}"


def norm_account(a):
    return "@" + (a or "").strip().lstrip("@").lower()


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40] or "x"


def http(method, url, data=None, headers=None, timeout=60):
    """Minimal HTTP helper. Honors HTTPS_PROXY and the system CA store."""
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        return exc.code, body, dict(exc.headers or {})


def url_resolves(url):
    """True when a cached media URL still serves an image."""
    try:
        status, body, hdrs = http("GET", url, headers={"Range": "bytes=0-2047"}, timeout=30)
    except Exception:
        return False
    if status not in (200, 206):
        return False
    ctype = (hdrs.get("Content-Type") or hdrs.get("content-type") or "").lower()
    return ctype.startswith("image/") or body[:8] == b"\x89PNG\r\n\x1a\n" or body[:3] == b"\xff\xd8\xff"


def rates():
    raw = os.environ.get("GPT_PILOT_RATES", "2.00,2.50,8.00")
    try:
        t, i, o = (float(x) for x in raw.split(","))
        return t, i, o
    except Exception:
        return 2.00, 2.50, 8.00


def cost_from_usage(usage):
    """USD for one generation from the API's usage object; None if absent."""
    if not isinstance(usage, dict):
        return None
    t_rate, i_rate, o_rate = rates()
    details = usage.get("input_tokens_details") or {}
    text_in = details.get("text_tokens", usage.get("input_tokens", 0) or 0)
    image_in = details.get("image_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    return (text_in * t_rate + image_in * i_rate + out * o_rate) / 1_000_000


# ----------------------------------------------------------------------------
# OpenAI generation
# ----------------------------------------------------------------------------
def openai_headers():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    hdrs = {"Content-Type": "application/json"}
    if key and key.lower() not in ("proxy", "placeholder", "injected"):
        hdrs["Authorization"] = f"Bearer {key}"
    return hdrs


def generate_background(prompt):
    """Return (PIL.Image RGB 1024x1536, cost_usd, usage_dict). Raises on failure."""
    if os.environ.get("GPT_PILOT_FAKE") == "1":
        time.sleep(0.2)
        return fake_background(prompt), 0.0, {"fake": True}

    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt + NO_TEXT_SUFFIX,
        "size": SIZE,
        "quality": QUALITY,
        "n": 1,
    }).encode()

    last = None
    for attempt in range(1, 5):
        try:
            status, body, _ = http("POST", OPENAI_URL, data=payload,
                                   headers=openai_headers(), timeout=240)
        except Exception as exc:  # network / timeout
            last = f"request failed: {exc}"
            time.sleep(5 * attempt)
            continue
        if status == 200:
            data = json.loads(body)
            b64 = (data.get("data") or [{}])[0].get("b64_json")
            if not b64:
                raise RuntimeError("OpenAI response had no image data")
            img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
            if img.size != (W, H):
                img = img.resize((W, H), Image.LANCZOS)
            usage = data.get("usage")
            cost = cost_from_usage(usage)
            if cost is None:
                cost = FALLBACK_COST.get(QUALITY, 0.013)
                usage = {"estimated": True}
            return img, cost, usage
        # non-200
        try:
            err = json.loads(body).get("error", {})
            msg = f"{err.get('type', '')}: {err.get('message', '')}".strip(": ")
        except Exception:
            msg = body[:300].decode("utf-8", "replace")
        last = f"HTTP {status} {msg}"
        if status in (429, 500, 502, 503, 504) and attempt < 4:
            time.sleep(10 * attempt)   # rate limit / transient: back off
            continue
        break  # 4xx other than 429: do not retry
    raise RuntimeError(f"OpenAI generation failed: {last}")


def fake_background(prompt):
    """Offline stand-in: a warm vertical gradient with a soft blob."""
    seed = int(hashlib.md5(prompt.encode()).hexdigest()[:6], 16)
    top = ((seed >> 16) & 0x7F) + 90, ((seed >> 8) & 0x5F) + 60, (seed & 0x3F) + 40
    bot = 40, 28, 20
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        col = tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3))
        for x in range(W):
            px[x, y] = col
    d = ImageDraw.Draw(img)
    d.ellipse((W * 0.15, H * 0.30, W * 0.85, H * 0.75), fill=(210, 150, 90))
    return img.filter(ImageFilter.GaussianBlur(6))


# ----------------------------------------------------------------------------
# text overlay (main thread only — PIL font objects are not shared across threads)
# ----------------------------------------------------------------------------
_font_cache = {}


def font(kind, size):
    """kind: 'display' (Anton) or 'body' (Oswald SemiBold). Falls back safely."""
    key = (kind, size)
    if key in _font_cache:
        return _font_cache[key]
    candidates = {
        "display": [os.path.join(FONT_DIR, "Anton-Regular.ttf"),
                    os.path.join(FONT_DIR, "Oswald[wght].ttf"),
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
        "body": [os.path.join(FONT_DIR, "Oswald[wght].ttf"),
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
    }[kind]
    f = None
    for path in candidates:
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size)
                if "Oswald" in path:
                    try:
                        f.set_variation_by_axes([600])
                    except Exception:
                        pass
                break
            except Exception:
                continue
    if f is None:
        f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def text_w(draw, s, f):
    l, t, r, b = draw.textbbox((0, 0), s, font=f)
    return r - l


def line_h(f):
    l, t, r, b = f.getbbox("Hg")
    return int((b - t) * 1.18)


def wrap(draw, s, f, max_w):
    """Greedy word wrap to pixel width."""
    words = s.split()
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if text_w(draw, cand, f) <= max_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit_font(draw, s, kind, max_w, start, floor, max_lines):
    """Largest size ≤ start whose wrapped text fits in max_lines."""
    size = start
    while size > floor:
        f = font(kind, size)
        lines = wrap(draw, s, f, max_w)
        if len(lines) <= max_lines and all(text_w(draw, l, f) <= max_w for l in lines):
            return f, lines
        size -= 4
    f = font(kind, floor)
    return f, wrap(draw, s, f, max_w)[:max_lines]


def parse_slide(idx, slide):
    """Return (header, body, bullets) using explicit fields or layout conventions."""
    header = slide.get("header")
    body = slide.get("body")
    bullets = slide.get("bullets")
    text = (slide.get("text") or "").strip()
    if header is None and body is None and bullets is None:
        for sep in (" — ", " – ", " - "):
            if sep in text:
                header, body = text.split(sep, 1)
                break
        else:
            if idx == 2 and ":" in text:
                header, body = text.split(":", 1)
            else:
                header, body = (text, "") if idx == 1 else (None, text)
        header = (header or "").strip()
        body = (body or "").strip()
        if idx == 2:
            header = header or "INGREDIENTS"
            parts = [p.strip(" .") for p in body.replace(";", ",").split(",")]
            bullets = [p for p in parts if p]
            body = ""
    return (header or "").strip(), (body or "").strip(), list(bullets or [])


def draw_panel(img, box, radius=36, alpha=150):
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.rounded_rectangle(box, radius=radius, fill=(0, 0, 0, alpha))
    overlay = overlay.filter(ImageFilter.GaussianBlur(2))
    return Image.alpha_composite(img.convert("RGBA"), overlay)


def draw_text_lines(draw, lines, f, x, y, fill=(255, 255, 255), stroke=3):
    lh = line_h(f)
    for ln in lines:
        draw.text((x, y), ln, font=f, fill=fill, stroke_width=stroke,
                  stroke_fill=(0, 0, 0))
        y += lh
    return y


def compose(bg, idx, slide, recipe):
    """Composite the slide text onto the background. Returns RGB image."""
    header, body, bullets = parse_slide(idx, slide)
    img = bg.convert("RGBA")
    draw = ImageDraw.Draw(img)
    max_w = SAFE_RIGHT - SAFE_LEFT - 2 * 36  # panel padding 36
    pad = 36
    accent = (255, 196, 61)  # warm amber for headers

    if idx == 1:
        # Title + hook: big display title, hook beneath. Block sits in the
        # upper-middle so the plated hero stays visible below.
        title = header.upper() or recipe.upper()
        tf, tlines = fit_font(draw, title, "display", max_w, 128, 72, 3)
        hf, hlines = fit_font(draw, body, "body", max_w, 54, 36, 3) if body else (None, [])
        block_h = len(tlines) * line_h(tf) + (len(hlines) * line_h(hf) + 20 if hlines else 0)
        y0 = SAFE_TOP + 60
        box = (SAFE_LEFT, y0, SAFE_RIGHT, y0 + block_h + 2 * pad)
        img = draw_panel(img, box, alpha=140)
        draw = ImageDraw.Draw(img)
        y = y0 + pad
        y = draw_text_lines(draw, tlines, tf, SAFE_LEFT + pad, y, fill=(255, 255, 255))
        if hlines:
            draw_text_lines(draw, hlines, hf, SAFE_LEFT + pad, y + 20, fill=accent, stroke=2)
        return img.convert("RGB")

    # Slides 2-5: header + body/bullets in a panel anchored to the bottom of
    # the safe area, so the top half of the food shot stays clean.
    hf, hlines = fit_font(draw, header.upper(), "display", max_w, 84, 52, 2) if header else (None, [])
    if bullets:
        bf = font("body", 46)
        blines = []
        for b in bullets[:8]:
            blines.extend(wrap(draw, "•  " + b, bf, max_w))
        if len(blines) > 9:
            bf = font("body", 40)
            blines = []
            for b in bullets[:8]:
                blines.extend(wrap(draw, "•  " + b, bf, max_w))
        body_lines, body_font = blines, bf
    elif body:
        if idx == 5:
            body_font, body_lines = fit_font(draw, body, "body", max_w, 50, 36, 4)
        else:
            body_font, body_lines = fit_font(draw, body, "body", max_w, 52, 36, 7)
    else:
        body_font, body_lines = None, []

    block_h = (len(hlines) * line_h(hf) if hlines else 0)
    if body_lines:
        block_h += (16 if hlines else 0) + len(body_lines) * line_h(body_font)
    y1 = SAFE_BOTTOM
    y0 = max(SAFE_TOP, y1 - block_h - 2 * pad)
    box = (SAFE_LEFT, y0, SAFE_RIGHT, y1)
    img = draw_panel(img, box, alpha=155)
    draw = ImageDraw.Draw(img)
    y = y0 + pad
    if hlines:
        y = draw_text_lines(draw, hlines, hf, SAFE_LEFT + pad, y, fill=accent)
        y += 16
    if body_lines:
        draw_text_lines(draw, body_lines, body_font, SAFE_LEFT + pad, y, stroke=2)
    return img.convert("RGB")


# ----------------------------------------------------------------------------
# spend ledger
# ----------------------------------------------------------------------------
def spend_add(date, images=0, cost=0.0, cached=0, posts=0):
    ledger = load_json(SPEND_PATH, {"schema_version": 1, "model": MODEL, "days": {}})
    day = ledger.setdefault("days", {}).setdefault(
        date, {"images_generated": 0, "cost_usd": 0.0, "cache_hits": 0, "posts": 0})
    day["images_generated"] += images
    day["cost_usd"] = round(day["cost_usd"] + cost, 6)
    day["cache_hits"] += cached
    day["posts"] += posts
    ledger["model"] = MODEL
    ledger["quality"] = QUALITY
    ledger["last_updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    save_json(SPEND_PATH, ledger)


# ----------------------------------------------------------------------------
# render
# ----------------------------------------------------------------------------
def validate_rows(rows):
    seen = set()
    for r in rows:
        for k in ("account", "recipe", "slides"):
            if k not in r:
                fail(f"row missing field '{k}': {json.dumps(r)[:120]}")
        if len(r["slides"]) != N_SLIDES:
            fail(f"{r['account']}: expected {N_SLIDES} slides, got {len(r['slides'])}")
        r["account"] = norm_account(r["account"])
        if r["account"] in seen:
            fail(f"duplicate account in plan: {r['account']}")
        seen.add(r["account"])


def render_rows(date, rows):
    """Render every row. Returns the manifest dict (per-row status)."""
    cache = load_json(CACHE_PATH, {"schema_version": 1, "entries": {}})
    entries = cache.setdefault("entries", {})
    cache_dirty = False

    manifest = {
        "date": date, "model": MODEL, "quality": QUALITY, "size": SIZE,
        "rows": [], "needs_upload": [],
        "rows_ok": 0, "rows_failed": 0, "cached": 0, "generated": 0,
        "dead_cache": 0, "total_cost_usd": 0.0,
    }

    # Phase 1: decide per slide (cache hit vs generate) — network checks only.
    tasks = []  # (row_i, slide_i, prompt)
    row_out = []
    for ri, row in enumerate(rows):
        account, recipe = row["account"], row["recipe"]
        style = row.get("visual_style_prompt", "")
        r = {"account": account, "recipe": recipe, "status": "ok", "error": None,
             "slides": [], "needs_upload": [], "cached": 0, "generated": 0,
             "cost_usd": 0.0}
        for si, slide in enumerate(row["slides"], start=1):
            key = cache_key(recipe, account, si)
            hit = entries.get(key)
            if hit and url_resolves(hit.get("url", "")):
                r["slides"].append({"index": si, "status": "cached", "url": hit["url"]})
                r["cached"] += 1
                continue
            if hit:
                manifest["dead_cache"] += 1
                entries.pop(key, None)
                cache_dirty = True
            r["slides"].append({"index": si, "status": "pending"})
            tasks.append((ri, si, slide.get("image") or f"{recipe}, {style}"))
        row_out.append(r)
        log(f"{account} {recipe}: {r['cached']} cached, {N_SLIDES - r['cached']} to generate")

    # Phase 2: fetch backgrounds concurrently (network-bound).
    results = {}
    if tasks:
        with futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            fut_map = {pool.submit(generate_background, p): (ri, si) for ri, si, p in tasks}
            for fut in futures.as_completed(fut_map):
                ri, si = fut_map[fut]
                try:
                    results[(ri, si)] = ("ok", fut.result())
                except Exception as exc:
                    results[(ri, si)] = ("error", str(exc))
                    log(f"{rows[ri]['account']} slide {si}: {exc}")

    # Phase 3: composite + save (main thread), per-row isolation.
    for ri, r in enumerate(row_out):
        row = rows[ri]
        out_dir = os.path.join(OUT_ROOT, date, r["account"].lstrip("@"))
        for s in r["slides"]:
            if s["status"] != "pending":
                continue
            si = s["index"]
            state, payload = results.get((ri, si), ("error", "no result"))
            if state != "ok":
                r["status"], r["error"] = "failed", f"slide {si}: {payload}"
                break
            bg, cost, usage = payload
            try:
                os.makedirs(out_dir, exist_ok=True)
                img = compose(bg, si, row["slides"][si - 1], r["recipe"])
                path = os.path.join(out_dir, f"slide_{si}.png")
                img.save(path, "PNG", optimize=True)
            except Exception as exc:
                r["status"], r["error"] = "failed", f"slide {si}: overlay failed: {exc}"
                break
            sha = hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
            s.update({"status": "generated", "path": path, "sha256_16": sha,
                      "cost_usd": round(cost, 6), "usage": usage})
            r["needs_upload"].append(si)
            r["generated"] += 1
            r["cost_usd"] = round(r["cost_usd"] + cost, 6)
        # Spend is real even for a failed row — count every generated image.
        manifest["generated"] += r["generated"]
        manifest["total_cost_usd"] = round(manifest["total_cost_usd"] + r["cost_usd"], 6)
        if r["status"] == "ok":
            manifest["rows_ok"] += 1
            manifest["cached"] += r["cached"]
            for si in r["needs_upload"]:
                manifest["needs_upload"].append({
                    "account": r["account"], "recipe": r["recipe"], "slide_index": si,
                    "filename": f"{slug(r['account'])}_{date}_slide_{si}.png",
                })
        else:
            manifest["rows_failed"] += 1
        manifest["rows"].append(r)
        log(f"GPT-ROW: {r['account']} {r['recipe']} status={r['status']} "
            f"cached={r['cached']} generated={r['generated']} cost=${r['cost_usd']:.4f} "
            f"needs_upload={r['needs_upload']}" + (f" error={r['error']}" if r["error"] else ""))

    if cache_dirty:
        save_json(CACHE_PATH, cache)
    if manifest["generated"] or manifest["cached"]:
        spend_add(date, images=manifest["generated"], cost=manifest["total_cost_usd"],
                  cached=manifest["cached"])
    return manifest


def cmd_render_batch(argv):
    if len(argv) != 1:
        fail("usage: gpt_pilot.py render-batch <plan.json>")
    plan = load_json(argv[0], None)
    if plan is None or "date" not in plan or not isinstance(plan.get("rows"), list) or not plan["rows"]:
        fail("plan.json needs 'date' and a non-empty 'rows' list")
    validate_rows(plan["rows"])
    manifest = render_rows(plan["date"], plan["rows"])
    manifest["batch"] = True
    save_json(MANIFEST_DEFAULT, manifest)
    log(f"GPT-PILOT-RENDER: rows_ok={manifest['rows_ok']} rows_failed={manifest['rows_failed']} "
        f"cached={manifest['cached']} generated={manifest['generated']} "
        f"cost=${manifest['total_cost_usd']:.4f} needs_upload={len(manifest['needs_upload'])} "
        f"manifest={MANIFEST_DEFAULT}")
    if manifest["rows_ok"] == 0:
        sys.exit(1)


def cmd_render(argv):
    if len(argv) != 1:
        fail("usage: gpt_pilot.py render <input.json>")
    spec = load_json(argv[0], None)
    if spec is None or "date" not in spec:
        fail("input.json needs 'date', 'account', 'recipe', 'slides'")
    row = {k: spec[k] for k in ("account", "recipe", "slides", "visual_style_prompt") if k in spec}
    validate_rows([row])
    manifest = render_rows(spec["date"], [row])
    manifest["batch"] = False
    save_json(MANIFEST_DEFAULT, manifest)
    r = manifest["rows"][0]
    if r["status"] != "ok":
        fail(r["error"])
    log(f"GPT-PILOT-RENDER: {r['account']} {r['recipe']} cached={r['cached']} "
        f"generated={r['generated']} cost=${r['cost_usd']:.4f} "
        f"needs_upload={r['needs_upload']} manifest={MANIFEST_DEFAULT}")


# ----------------------------------------------------------------------------
# upload
# ----------------------------------------------------------------------------
def upload_rows(manifest, uploads):
    by_key = {}
    single = len(manifest["rows"]) == 1
    for u in uploads:
        try:
            acct = norm_account(u.get("account") or (manifest["rows"][0]["account"] if single else ""))
            by_key[(acct, int(u["slide_index"]))] = u
        except Exception:
            fail(f"bad uploads entry: {json.dumps(u)[:200]}")

    date = manifest["date"]
    cache = load_json(CACHE_PATH, {"schema_version": 1, "entries": {}})
    entries = cache.setdefault("entries", {})
    out = {"date": date, "rows": [], "rows_ok": 0, "rows_failed": 0, "uploaded": 0,
           "total_cost_usd": manifest.get("total_cost_usd", 0.0)}

    for r in manifest["rows"]:
        res = {"account": r["account"], "recipe": r["recipe"], "status": r["status"],
               "error": r.get("error"), "mediaUrls": None, "uploaded": 0,
               "cached": r.get("cached", 0), "generated": r.get("generated", 0),
               "cost_usd": r.get("cost_usd", 0.0)}
        if r["status"] != "ok":
            out["rows"].append(res)
            out["rows_failed"] += 1
            continue
        media = [None] * N_SLIDES
        err = None
        for s in r["slides"]:
            i = s["index"]
            if s["status"] == "cached":
                media[i - 1] = s["url"]
                continue
            u = by_key.get((r["account"], i))
            if not u or not u.get("presignedUrl") or not u.get("publicUrl"):
                err = f"slide {i}: no presigned/public URL in uploads.json"
                break
            path = s.get("path")
            if not path or not os.path.exists(path):
                err = f"slide {i}: file missing: {path}"
                break
            data = open(path, "rb").read()
            try:
                status, body, _ = http("PUT", u["presignedUrl"], data=data,
                                       headers={"Content-Type": "image/png"}, timeout=120)
            except Exception as exc:
                err = f"slide {i}: upload request failed: {exc}"
                break
            if status not in (200, 201, 204):
                err = f"slide {i}: upload returned HTTP {status} {body[:120]!r}"
                break
            if not url_resolves(u["publicUrl"]):
                err = f"slide {i}: public URL does not resolve after upload"
                break
            media[i - 1] = u["publicUrl"]
            entries[cache_key(r["recipe"], r["account"], i)] = {
                "url": u["publicUrl"], "created": date,
                "sha256_16": s.get("sha256_16"), "model": manifest.get("model"),
                "quality": manifest.get("quality"),
            }
            res["uploaded"] += 1
        if err is None and any(m is None for m in media):
            err = "mediaUrls incomplete"
        if err:
            res["status"], res["error"] = "failed", err
            out["rows_failed"] += 1
            log(f"GPT-ROW: {r['account']} {r['recipe']} status=failed error={err}")
        else:
            res["mediaUrls"] = media
            out["rows_ok"] += 1
            out["uploaded"] += res["uploaded"]
            log(f"GPT-MEDIA: {r['account']} " + " ".join(media))
        out["rows"].append(res)

    cache["last_updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    save_json(CACHE_PATH, cache)  # partial progress is still valid cache
    if out["rows_ok"]:
        spend_add(date, posts=out["rows_ok"])
    return out


def cmd_upload_batch(argv):
    if len(argv) != 2:
        fail("usage: gpt_pilot.py upload-batch <pilot_manifest.json> <uploads.json>")
    manifest = load_json(argv[0], None)
    uploads = load_json(argv[1], None)
    if manifest is None or uploads is None:
        fail("manifest or uploads file not found")
    out = upload_rows(manifest, uploads)
    save_json(MEDIA_DEFAULT, out)
    log(f"GPT-PILOT-UPLOAD: rows_ok={out['rows_ok']} rows_failed={out['rows_failed']} "
        f"uploaded={out['uploaded']} cost=${out['total_cost_usd']:.4f} media={MEDIA_DEFAULT}")
    if out["rows_ok"] == 0:
        sys.exit(1)


def cmd_upload(argv):
    if len(argv) != 2:
        fail("usage: gpt_pilot.py upload <pilot_manifest.json> <uploads.json>")
    manifest = load_json(argv[0], None)
    uploads = load_json(argv[1], None)
    if manifest is None or uploads is None:
        fail("manifest or uploads file not found")
    out = upload_rows(manifest, uploads)
    r = out["rows"][0]
    result = {"date": out["date"], "account": r["account"], "recipe": r["recipe"],
              "mediaUrls": r["mediaUrls"], "uploaded": r["uploaded"],
              "cost_usd": r["cost_usd"], "rows": out["rows"]}
    save_json(MEDIA_DEFAULT, result)
    if r["status"] != "ok":
        fail(r["error"])
    log("GPT-PILOT-MEDIA: " + " ".join(r["mediaUrls"]))
    log(f"GPT-PILOT-UPLOAD: {r['account']} {r['recipe']} uploaded={r['uploaded']} "
        f"cached={r['cached']} cost=${r['cost_usd']:.4f} media={MEDIA_DEFAULT}")


def cmd_status(argv):
    cache = load_json(CACHE_PATH, {"entries": {}})
    ledger = load_json(SPEND_PATH, {"days": {}})
    log(f"cache entries: {len(cache.get('entries', {}))}")
    total = sum(d.get("cost_usd", 0.0) for d in ledger.get("days", {}).values())
    for day, d in sorted(ledger.get("days", {}).items()):
        log(f"  {day}: {d.get('posts', 0)} post(s), {d.get('images_generated', 0)} images, "
            f"{d.get('cache_hits', 0)} cache hits, ${d.get('cost_usd', 0.0):.4f}")
    log(f"total spend: ${total:.4f} ({ledger.get('model', MODEL)}, {ledger.get('quality', QUALITY)})")


COMMANDS = {
    "render-batch": cmd_render_batch, "upload-batch": cmd_upload_batch,
    "render": cmd_render, "upload": cmd_upload, "status": cmd_status,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        fail("usage: gpt_pilot.py render-batch <plan> | upload-batch <manifest> <uploads> | "
             "render <input> | upload <manifest> <uploads> | status")
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
