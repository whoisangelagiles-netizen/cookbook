#!/usr/bin/env python3
"""
gpt_pilot.py — GPT Image mini pilot: external slide generation for ONE account.

Replaces Blotato AI visual generation for the pilot account (@postworkout.plate)
with OpenAI Images (gpt-image-1-mini) + Pillow text overlay, then hands the
finished slides to Blotato as own media (which schedules at zero credit cost).

Subcommands
-----------
  render <input.json>
      For each of the 5 slides: reuse a cached, still-resolving media URL when
      one exists for (recipe, account, slide_index); otherwise generate the
      background with OpenAI and composite the slide text with Pillow.
      Writes out/gpt-pilot/<date>/<account>/slide_N.png and a manifest
      (pilot_manifest.json). Prints one GPT-PILOT-RENDER summary line.

  upload <pilot_manifest.json> <uploads.json>
      uploads.json is written by the routine after calling
      blotato_create_presigned_upload_url once per slide that needs upload:
        [{"slide_index": 1, "presignedUrl": "...", "publicUrl": "..."}, ...]
      PUTs each PNG, verifies the public URL resolves, records it in
      state/media-cache.json, writes pilot_media.json with the final 5-URL
      mediaUrls array (cached + freshly uploaded, in slide order) and prints
      one GPT-PILOT-MEDIA line.

  status
      Print cache size and the spend ledger.

Exit codes: 0 = success. 1 = failure — the routine MUST fall back to normal
Blotato generation for this post and log the reason. The script never
schedules posts, never touches the recipe rotation, and never deletes anything.

Input JSON for `render` (the routine composes it from the same content it
would send to blotato_create_visual):
{
  "date": "2026-09-10",
  "account": "@postworkout.plate",
  "recipe": "Greek Lamb Bowl",
  "visual_style_prompt": "soft warm golden hour lighting, ...",
  "slides": [
    {"image": "<image prompt, same as the Blotato slide prompt>",
     "text":  "<slide text, same as the Blotato slide text>"},
    ... exactly 5 ...
  ]
}
Optional per-slide overrides: "header", "body", "bullets" (list). When absent
the text is parsed with the same conventions as the locked 5-slide layout
("HEADER — body", "Ingredients: a, b, c").

Auth: the OpenAI key is attached to the cloud environment as an API credential
that the egress proxy injects for api.openai.com. When OPENAI_API_KEY is unset
or holds the placeholder value "proxy", the script sends NO Authorization
header and lets the proxy add it (verified 2026-09-09). Any other value is sent
as a Bearer token, for local use. The key is never printed or written anywhere.

Environment knobs (all optional):
  GPT_PILOT_MODEL     default gpt-image-1-mini
  GPT_PILOT_QUALITY   low | medium | high   (default medium)
  GPT_PILOT_RATES     "text_in,image_in,image_out" USD per 1M tokens, used to
                      turn the API's usage object into a cost figure.
                      Default 2.00,2.50,8.00 — VERIFY at openai.com/api/pricing
                      and override here if it differs.
  GPT_PILOT_FAKE=1    skip OpenAI, paint a gradient background (offline tests)
"""

import base64
import datetime as dt
import hashlib
import io
import json
import os
import ssl
import sys
import textwrap
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
SIZE = "1024x1536"          # closest OpenAI size to 9:16
W, H = 1024, 1536
N_SLIDES = 5
OPENAI_URL = "https://api.openai.com/v1/images/generations"

# Fallback per-image estimates when the API returns no usage object.
FALLBACK_COST = {"low": 0.005, "medium": 0.011, "high": 0.04}

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
def log(msg):
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
        return fake_background(prompt), 0.0, {"fake": True}

    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt + NO_TEXT_SUFFIX,
        "size": SIZE,
        "quality": QUALITY,
        "n": 1,
    }).encode()

    last = None
    for attempt in range(1, 4):
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
                cost = FALLBACK_COST.get(QUALITY, 0.011)
                usage = {"estimated": True}
            return img, cost, usage
        # non-200
        try:
            err = json.loads(body).get("error", {})
            msg = f"{err.get('type', '')}: {err.get('message', '')}".strip(": ")
        except Exception:
            msg = body[:300].decode("utf-8", "replace")
        last = f"HTTP {status} {msg}"
        if status in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(8 * attempt)
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
# text overlay
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
        # shrink if too tall
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
# commands
# ----------------------------------------------------------------------------
def cmd_render(argv):
    if len(argv) != 1:
        fail("usage: gpt_pilot.py render <input.json>")
    spec = load_json(argv[0], None)
    if spec is None:
        fail(f"input not found: {argv[0]}")
    for k in ("date", "account", "recipe", "slides"):
        if k not in spec:
            fail(f"input missing field '{k}'")
    slides = spec["slides"]
    if len(slides) != N_SLIDES:
        fail(f"expected {N_SLIDES} slides, got {len(slides)}")
    date, account, recipe = spec["date"], spec["account"], spec["recipe"]
    style = spec.get("visual_style_prompt", "")

    cache = load_json(CACHE_PATH, {"schema_version": 1, "entries": {}})
    entries = cache.setdefault("entries", {})
    out_dir = os.path.join(OUT_ROOT, date, account.lstrip("@"))
    os.makedirs(out_dir, exist_ok=True)

    manifest = {
        "date": date, "account": account, "recipe": recipe,
        "model": MODEL, "quality": QUALITY, "size": SIZE,
        "slides": [], "needs_upload": [], "total_cost_usd": 0.0,
        "cached": 0, "generated": 0, "dead_cache": 0,
    }

    for i, slide in enumerate(slides, start=1):
        key = cache_key(recipe, account, i)
        hit = entries.get(key)
        if hit and url_resolves(hit.get("url", "")):
            manifest["slides"].append({"index": i, "status": "cached", "url": hit["url"]})
            manifest["cached"] += 1
            log(f"slide {i}: cache hit")
            continue
        if hit:
            manifest["dead_cache"] += 1
            log(f"slide {i}: cached URL no longer resolves — regenerating")
            entries.pop(key, None)

        prompt = slide.get("image") or f"{recipe}, {style}"
        try:
            bg, cost, usage = generate_background(prompt)
        except Exception as exc:
            fail(f"slide {i}: {exc}")
        try:
            img = compose(bg, i, slide, recipe)
        except Exception as exc:
            fail(f"slide {i}: overlay failed: {exc}")
        path = os.path.join(out_dir, f"slide_{i}.png")
        img.save(path, "PNG", optimize=True)
        sha = hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
        manifest["slides"].append({
            "index": i, "status": "generated", "path": path, "sha256_16": sha,
            "cost_usd": round(cost, 6), "usage": usage,
        })
        manifest["needs_upload"].append(i)
        manifest["generated"] += 1
        manifest["total_cost_usd"] = round(manifest["total_cost_usd"] + cost, 6)
        log(f"slide {i}: generated (${cost:.4f})")

    if manifest["dead_cache"]:
        save_json(CACHE_PATH, cache)
    spend_add(date, images=manifest["generated"], cost=manifest["total_cost_usd"],
              cached=manifest["cached"])
    save_json(MANIFEST_DEFAULT, manifest)
    log(f"GPT-PILOT-RENDER: {account} {recipe} cached={manifest['cached']} "
        f"generated={manifest['generated']} cost=${manifest['total_cost_usd']:.4f} "
        f"needs_upload={manifest['needs_upload']} manifest={MANIFEST_DEFAULT}")


def cmd_upload(argv):
    if len(argv) != 2:
        fail("usage: gpt_pilot.py upload <pilot_manifest.json> <uploads.json>")
    manifest = load_json(argv[0], None)
    uploads = load_json(argv[1], None)
    if manifest is None or uploads is None:
        fail("manifest or uploads file not found")
    by_idx = {}
    for u in uploads:
        try:
            by_idx[int(u["slide_index"])] = u
        except Exception:
            fail(f"bad uploads entry: {u!r}")

    date, account, recipe = manifest["date"], manifest["account"], manifest["recipe"]
    cache = load_json(CACHE_PATH, {"schema_version": 1, "entries": {}})
    entries = cache.setdefault("entries", {})
    media = [None] * N_SLIDES
    uploaded = 0

    for s in manifest["slides"]:
        i = s["index"]
        if s["status"] == "cached":
            media[i - 1] = s["url"]
            continue
        u = by_idx.get(i)
        if not u or not u.get("presignedUrl") or not u.get("publicUrl"):
            fail(f"slide {i} needs upload but uploads.json has no presigned/public URL for it")
        path = s["path"]
        if not os.path.exists(path):
            fail(f"slide {i}: file missing: {path}")
        data = open(path, "rb").read()
        try:
            status, body, _ = http("PUT", u["presignedUrl"], data=data,
                                   headers={"Content-Type": "image/png"}, timeout=120)
        except Exception as exc:
            fail(f"slide {i}: upload request failed: {exc}")
        if status not in (200, 201, 204):
            fail(f"slide {i}: upload returned HTTP {status} {body[:200]!r}")
        if not url_resolves(u["publicUrl"]):
            fail(f"slide {i}: public URL does not resolve after upload")
        media[i - 1] = u["publicUrl"]
        entries[cache_key(recipe, account, i)] = {
            "url": u["publicUrl"], "created": date,
            "sha256_16": s.get("sha256_16"), "model": manifest.get("model"),
            "quality": manifest.get("quality"),
        }
        uploaded += 1
        log(f"slide {i}: uploaded + verified")

    if any(m is None for m in media):
        fail("mediaUrls incomplete — not all 5 slides resolved")
    cache["last_updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    save_json(CACHE_PATH, cache)
    spend_add(date, posts=1)
    result = {"date": date, "account": account, "recipe": recipe,
              "mediaUrls": media, "uploaded": uploaded,
              "cost_usd": manifest.get("total_cost_usd", 0.0)}
    save_json(MEDIA_DEFAULT, result)
    log("GPT-PILOT-MEDIA: " + " ".join(media))
    log(f"GPT-PILOT-UPLOAD: {account} {recipe} uploaded={uploaded} "
        f"cached={manifest.get('cached', 0)} cost=${result['cost_usd']:.4f} "
        f"media={MEDIA_DEFAULT}")


def cmd_status(argv):
    cache = load_json(CACHE_PATH, {"entries": {}})
    ledger = load_json(SPEND_PATH, {"days": {}})
    log(f"cache entries: {len(cache.get('entries', {}))}")
    total = sum(d.get("cost_usd", 0.0) for d in ledger.get("days", {}).values())
    for day, d in sorted(ledger.get("days", {}).items()):
        log(f"  {day}: {d.get('posts', 0)} post(s), {d.get('images_generated', 0)} images, "
            f"{d.get('cache_hits', 0)} cache hits, ${d.get('cost_usd', 0.0):.4f}")
    log(f"total spend: ${total:.4f} ({ledger.get('model', MODEL)}, {ledger.get('quality', QUALITY)})")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("render", "upload", "status"):
        fail("usage: gpt_pilot.py render <input.json> | upload <manifest> <uploads> | status")
    {"render": cmd_render, "upload": cmd_upload, "status": cmd_status}[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
