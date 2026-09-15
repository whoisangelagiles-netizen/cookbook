# cookbook-daily-3x — Claude Code Cloud Routine

**Purpose:** Daily production loop for "High Protein House" TikTok automation. 10 accounts, 1 post/day each at 18:00 ET, 5 slides per post, warmup or launch mode (per-account), draws from the full 110-recipe rotation across all categories. Migrated from Cowork on 2026-07-27.

**Schedule:** Daily at 4 AM America/New_York (recommended). No end date.

**Connectors required:** Blotato + Slack (Slack is used only for the low-credit alert in STEP 2.6; a Slack failure never blocks posting).

**Repo:** `whoisangelagiles-netizen/cookbook`

**Files this routine reads/writes (paths are repo-root-relative — the routine runs from the repo root):**
- `state/recipe-rotation-log.json` (read + write)
- `state/account-voices.json` (read only)
- `state/automation-log.md` (append only)
- Google Sheets: HPH Operations Tracker `GrowthDaily` + `PostLog` tabs, written by `scripts/append_stats.py` in STEP 6 (best-effort)

---

## Task prompt (paste this into the routine's prompt field)

You are the ongoing daily production loop for "High Protein House" TikTok automation, running in Claude Code Cloud. NO end date. All state lives in this git repo — read state at the start, mutate in memory during the run, commit updated state at the end.

**CADENCE (changed 2026-08-11 — all accounts are now `1x`):**
- ONE slot per day: **18:00 ET**. All 10 accounts post once.
- Time = 18:00 ET + account_index × 3 min stagger (alphabetical by handle → 18:00–18:27).
- Breakfast and Lunch slots are REMOVED. There is no 08:00 or 12:00 slot. Do not reintroduce them.
- Posts are **5 slides**, not 6.
- **GENERATION (from 2026-09-14): ALL accounts** get their 5 slides from OpenAI gpt-image-1-mini + a Pillow text overlay (`scripts/gpt_pilot.py`, batch mode) and post them as own media at **zero Blotato credits**. Blotato `blotato_create_visual` is the **fallback only**, used for rows the script marks failed. The @postworkout.plate pilot (2026-09-10 → 09-13) ran 4/4 days with no fallbacks at $0.064/post, versus ~70 Blotato credits (~$0.42) per post. Rows are isolated: one failing row falls back on its own; the rest stay on GPT.

Why: Blotato bills per slide, and by late August the observed rate was ~14 credits/slide (~700 credits/day for 10 posts, ~21,000/month against a ~5,000/month grant). The old 28-post, 6-slide day was worse still (~35,000/month, which is why runs silently truncated and Breakfast disappeared in June). GPT generation removes that cost for every row that does not fall back — see the COST MODEL at the end.

**PER-ACCOUNT MODE (from `state/account-voices.json`):**
- "warmup" (7 accounts): slide 5 macros only, caption no CTA.
- "launch" (currently @fuel.your.gains, @gymfood.simple, @prep.with.alex): slide 5 adds Gumroad CTA, caption adds "Full cookbook in bio ⬇️".

**★ PAST-SLOT SKIP GUARD:** If a slot's base time is more than 4 hours past current time (America/New_York), SKIP that entire slot for today. Do NOT generate visuals, do NOT schedule posts, do NOT advance recipe rotation for skipped slots. This prevents a late manual run from posting at an unintended hour. If a slot is past but within 4 hours, bump to next round hour ≥30 min from now keeping stagger.

**★ TEMPLATE (locked):** Blotato "Image Slideshow with Prominent Text" `/base/v2/images-with-text/0ddb8655-c3da-43da-9f7d-be1915ca7818/v1`. Schema is `image` + `text` per slide.

---

**STEP 1 — Load state**
Read `state/recipe-rotation-log.json` (recipes with category + calories + protein_g + last_posted). Read `state/account-voices.json` (per-account voice/mode/cadence/bio config). Compute today ISO date + current time in America/New_York.

**STEP 2 — Get connected TikTok accounts via Blotato**
Call `blotato_list_accounts` with platform="tiktok". Build handle→id map. Known mapping:
- @cleanfuel.kitchen → getfuel.kitchen (45078)
- @coach.macro → macro.coaching (44058)
- @fuel.your.gains → fuel.your.gains (45081)
- @gymfood.simple → gymfoodsimple (45080)
- @macro.architect → macro.architect (44892)
- @postworkout.plate → postworkout.plate (44894)
- @prep.with.alex → prep.with.alex (44893)
- @protein.lab.eats → proteinlabseat (45082)
- @the.lean.cook → theleancook5 (45079)
- @under10.protein → under10.protein (45077)
Skip @angelagiles29/41416 if present in the account list.

**STEP 2.5 — Credit preflight (Blotato credits are for fallbacks only)**
Call `blotato_get_credits` and record the balance. The GPT path consumes **no** Blotato credits. Credits are spent only when a row falls back to `blotato_create_visual`, and the observed rate since 2026-08-21 is **~14 credits/slide, ~70 per 5-slide post** (the 7/slide figure from 2026-08-11 no longer holds — three consecutive runs logged 675, 690 and 735 credits for 10 posts).

**Hard floor for fallbacks — never start a visual you cannot finish.** Begin a fallback row only when at least 70 credits remain, and re-check the balance before each further fallback row. A partially-rendered visual returns fewer than 5 `imageUrls`, and posting that array ships a broken carousel (2026-07-20). If a fallback row cannot be afforded, log `insufficient-credits` for that row with the balance and move on — do NOT retry, and do NOT let it stop the GPT rows.

**STEP 2.6 — (removed 2026-09-15)** The Blotato low-credit Slack alert and `blotato_buy_credits` checkout links are gone. Blotato credits only matter for fallback rows now, and the health of the run is reported by STEP 4.9 instead. Do not post about Blotato credits and do not generate checkout links.

**STEP 3 — Assign recipes**
There is ONE slot (18:00 ET) and ALL 10 accounts participate.

- Check the past-slot skip guard first. If 18:00 ET is more than 4h past, skip the day entirely: generate nothing, schedule nothing, advance no `last_posted`.
- **Draw from ALL categories.** Sort the ENTIRE recipe set — breakfast, lunch, dinner and snack together — by (last_posted asc, name asc). This is the full 110-recipe rotation, not dinner-only. The voice templates work for any category, and a wider pool means each recipe resurfaces far less often.
- **Per-category cap of 3.** Walk the sorted list and take recipes in order, but skip a recipe once its category already has 3 in the pool; stop at 10 (N = 10 accounts). Without the cap, an untouched category floods the whole slot (all 10 rows were snacks on 2026-08-11). Log the resulting mix in the summary line.
- For each account in alphabetical order (account_index 0..9):
    `recipe = pool[(account_index + today_day_of_year) % N]`

**STEP 4 — Build every row, generate all slides in one batch, then schedule**
Order: alphabetical by handle, account_index 0..9, all in the single 18:00 ET slot.

**4a–4d run for every row first (no API calls yet).** Then 4e generates all rows at once. Then 4g–4i schedule row by row.

4a) Cook time: use the recipe's own category to pick a sensible figure — breakfast 8 min, lunch 10 min, dinner 12 min, snack 5 min. This feeds {TIME} in the hook template.

4b) Build VARIANT_HOOK from account's `hook_template` (substitute {PROTEIN}, {CAL}, {TIME}).

4c) Compute scheduledTime:
    - Base time (America/New_York today at 18:00) + (account_index × 3 min)
    - If past current time (but within 4 hours), bump to next round hour ≥30 min from now keeping stagger.

4d) Compose the 5 slides — `image` prompt (20-400 chars) + `text` (30-200 chars) per slide, food-forward (food fills the frame — no generic hands-in-kitchen). The same content feeds the GPT path and, if needed, the Blotato fallback.

    **LOCKED 5-SLIDE LAYOUT (changed 2026-08-11 from 6):**
    - Slide 1 (TITLE + HOOK): image = "Hero close-up of the finished {recipe}, {visual_style_prompt}, vertical 9:16, glistening and beautifully plated" / text = "{RECIPE UPPERCASE} — {VARIANT_HOOK}"
    - Slide 2 (INGREDIENTS): image = "Overhead flat-lay of raw ingredients for {recipe} on a dark wooden board, {visual_style_prompt}, vertical 9:16" / text = "Ingredients: {5-7 items with quantities}"
    - Slide 3 (STEPS 1-2 — combined): image = "Tight close-up of {first prep step for {recipe}}, food fills the frame, {visual_style_prompt}, vertical 9:16" / text = "STEPS 1-2 — {action one, then action two}"
    - Slide 4 (FINAL STEPS): image = "Tight close-up of {finishing step}, food is the subject, {visual_style_prompt}, vertical 9:16" / text = "STEP 3 — {finishing action}"
    - Slide 5 (FINISHED + MACROS — mode-driven, carries the CTA):
        - image: "Final beautifully plated {recipe} as the full background, {visual_style_prompt}, restaurant quality, vertical 9:16"
        - text if mode == "warmup": "{Cal} CAL · {Protein}G PROTEIN — Real food, real macros. High Protein House." (≥30 chars, no CTA)
        - text if mode == "launch": "{Cal} CAL · {Protein}G PROTEIN — Want more? Full Cookbook in Bio ⬇️" (≥30 chars, CTA on)

    The old 6-slide layout split prep across three slides. Do not emit 6 slides. The overlay script draws `text` itself, so the image prompts must describe food only; it appends its own "no text, no people" instruction.

**4e) GPT generation — ALL rows in one batch (default path, zero Blotato credits)**

e1) Write `pilot_plan.json` in the repo root with every participating row:
```
{"date": "{today_iso}",
 "rows": [{"account": "@handle", "recipe": "{recipe}",
           "visual_style_prompt": "{visual_style_prompt}",
           "slides": [{"image": "...", "text": "..."}, ... exactly 5 ...]},
          ... one per row ...]}
```

e2) Install the overlay dependency, then render everything:
```
python3 -c "import PIL" 2>/dev/null || pip install --quiet pillow 2>&1 | tail -1 || true
python3 scripts/gpt_pilot.py render-batch pilot_plan.json
```
**The install line is required** — the routine container does not ship Pillow. The script reuses cached slides whose media URL still resolves (`state/media-cache.json`, keyed recipe + account + slide index), generates the rest with OpenAI (4 concurrent requests, 1024×1536, no text — the overlay is drawn locally), and prints one `GPT-ROW:` line per row plus a final `GPT-PILOT-RENDER:` summary. It writes `pilot_manifest.json`. Expect roughly 3–6 minutes for 50 fresh images.

The OpenAI key is an API credential on the cloud environment; the egress proxy injects it for api.openai.com. The script sends no key of its own when `OPENAI_API_KEY` is the placeholder value `proxy`. Never print, log or commit any key.

e3) Read `pilot_manifest.json`. Every row with `"status": "failed"` goes on the **fallback list** with its `error`. For each entry in the manifest's `needs_upload` list, call `blotato_create_presigned_upload_url` with that entry's `filename` and collect `{"account": entry.account, "slide_index": entry.slide_index, "presignedUrl": ..., "publicUrl": ...}`. Write them all to `pilot_uploads.json` (an empty list `[]` if nothing needs uploading). This is up to 50 calls on a cache-cold day; make them all before moving on.

e4) Run `python3 scripts/gpt_pilot.py upload-batch pilot_manifest.json pilot_uploads.json`. It PUTs each PNG, verifies every public URL serves an image, records URLs in `state/media-cache.json`, updates `state/gpt-pilot-spend.json`, prints one `GPT-MEDIA: @handle url1 … url5` line per successful row and a `GPT-PILOT-UPLOAD:` summary, and writes `pilot_media.json`.

e5) Read `pilot_media.json`. For each row with `"status": "ok"`, `post_mediaUrls` = its `mediaUrls` (5 URLs, in slide order). Rows with `"status": "failed"` join the fallback list.

**If either script exits 1, hangs past 15 minutes, or its output file is missing, every row not yet resolved goes on the fallback list.** Do not retry the GPT path within a run. Do not delete or hand-edit `state/media-cache.json`.

**4f) FALLBACK rows only — Blotato AI generation (costs ~70 credits per row)**

For each row on the fallback list, and only those, subject to the STEP 2.5 floor:

f1) Generate via `blotato_create_visual`:
    - templateId: `/base/v2/images-with-text/0ddb8655-c3da-43da-9f7d-be1915ca7818/v1` (locked)
    - title: `{recipe_cleaned}_{today_iso}_{handle_cleaned}`
    - render: true
    - inputs.aspectRatio: "9:16", inputs.slideDuration: 5
    - inputs.slides: the same 5 `image` + `text` slides from 4d (the template accepts 5 and returns exactly 5 `imageUrls`).

f2) Poll `blotato_get_visual_status` with parameter `id` (NOT `visualId`) every 30 sec, up to 12 attempts (6 min). On timeout or `insufficient-credits`, log the row + skip. If >3 consecutive credit errors, stop starting fallback rows (GPT rows are unaffected).

f3) `post_mediaUrls = imageUrls` (FULL 5-URL array — never just imageUrls[0]).

f4) Log `[GPT] FALLBACK @handle: {reason from the manifest/media file}` in the summary notes for every fallback row, whether or not the fallback itself succeeded.

4g) Build caption (mode-driven):
    - warmup:
      ```
      {VARIANT_HOOK} 💪

      {caption_opener} {recipe}
      {Cal} cal | {Protein}g protein

      {hashtag_stack}
      ```
    - launch:
      ```
      {VARIANT_HOOK} 💪

      {caption_opener} {recipe}
      {Cal} cal | {Protein}g protein

      Full cookbook in bio ⬇️

      {hashtag_stack}
      ```

4h) Schedule via `blotato_create_post`:
    - accountId, platform="tiktok"
    - text = caption
    - mediaUrls = full 5-URL array (from 4e for GPT rows, from 4f for fallback rows)
    - scheduledTime = ISO 8601 with America/New_York offset
    - privacyLevel = "PUBLIC_TO_EVERYONE"
    - disabledComments = false, disabledDuet = false, disabledStitch = false
    - isBrandedContent = false, isYourBrand = true
    - isAiGenerated = true (REQUIRED — TikTok AI disclosure)
    - title = first 80 chars of VARIANT_HOOK

4i) On success, update the recipe's `last_posted` = today ISO in the in-memory rotation-log dict.

4j) On row failure, log and continue. On >3 consecutive credit/cap errors, log critical + stop.

**STEP 4.9 — Generation-health & OpenAI budget alert (Slack #tech)**

Runs after every row has been handled (4e–4j), before STEP 5. This replaces the old Blotato credit alert.

k1) Run:
```
python3 scripts/gpt_pilot.py budget-check --date {today_iso} --missed {comma-separated handles whose slot was NOT filled by either path, or omit the flag}
```
It reads `state/gpt-pilot-spend.json` and today's `pilot_manifest.json` / `pilot_media.json`, prints exactly one `GPT-ALERT:` line and writes `pilot_alert.json`. It always exits 0.

k2) If the line is `GPT-ALERT: none`, post nothing. Otherwise read `pilot_alert.json` and post ONE message to Slack `#tech` (ID `C0ARUTE3PPC`) via `slack_send_message`. Triggers (`reasons`):
- `billing` — OpenAI refused generation today with a billing/quota/auth error (`billing_failures`). Those rows fell back to Blotato at ~70 credits each; the fix is on the OpenAI side.
- `budget` — month-to-date spend is at or past `warn_pct` of `budget_usd`, or the projection for the month exceeds the budget. Budget comes from the environment variable `OPENAI_MONTHLY_BUDGET_USD` (default 30) — keep it equal to the monthly limit set in OpenAI billing.
- `missed` — a slot was not filled by either path (GPT failed AND the Blotato fallback could not run).

Message format (fill from `pilot_alert.json`; omit lines whose trigger is absent):

```
:warning: *High Protein House — image generation ({reasons})*

OpenAI spend this month: *${mtd_usd}* of *${budget_usd}* ({mtd_pct}%), {days_left} days left — projected *${projected_month_usd}*
Today: {today_posts} posts, {today_images} images, ${today_cost_usd}

{if billing}: *OpenAI refused generation for {n} row(s):* {account}: {error} — these fell back to Blotato (~70 credits each).
{if missed}: *Missed slot(s) today:* {missed handles} — neither OpenAI nor the Blotato fallback could produce them.

Action: platform.openai.com → Settings → Billing — add prepaid credit and/or raise the monthly limit (then update OPENAI_MONTHLY_BUDGET_USD on the Routine environment to match). Fallbacks cost ~70 Blotato credits each; balance now {blotato_balance}.
```

k3) If Slack is unavailable or the call fails, log `SLACK-ERROR: <reason>` (or `SLACK-UNAVAILABLE`) to `state/automation-log.md` and continue. **The alert must never block the state commit.** Post at most one message per run and never on a healthy run.

**STEP 5 — Persist state (git commit + push)**
`git add state/` also picks up `state/media-cache.json` and `state/gpt-pilot-spend.json` when the pilot ran. Write updated `state/recipe-rotation-log.json` with new `last_posted` values (only for recipes successfully scheduled — leave skipped recipes untouched so they surface first next run). Refresh `last_updated` field.

Append a one-line summary to `state/automation-log.md`. The notes MUST include the batch tag `[GPT] rows_ok={n} rows_failed={m} generated={g} cached={c} cost=${x}` copied from the `GPT-PILOT-RENDER:`/`GPT-PILOT-UPLOAD:` lines, plus one `[GPT] FALLBACK @handle: reason` per fallback row, and the `GPT-ALERT:` line from STEP 4.9 — this is the spend and reliability record:
```
- [{timestamp}] DAILY-3X (mixed cadence + mode): {connected}/{expected} accounts, cadences [{3x_count}×3x + {2x_count}×2x], slots-fired [{slots_fired}], slots-skipped-past [{slots_skipped}], {visuals} visuals, {posts_b}B + {posts_l}L + {posts_d}D scheduled ({posts_total} total). {errors} errors. Launch CTAs on: [handles]. Credits remaining: {credits_remaining}. {notes}
```

Then commit + push:
```
git add state/
git commit -m "daily run {today_iso}: {posts_total} posts scheduled"
git push origin HEAD:main
```

**Use `HEAD:main`, never `origin main`.** The scheduled session may be checked out on an auto-generated outcome branch (e.g. `claude/gallant-lovelace`) rather than on `main`. `git push origin main` pushes the *local* `main` ref, which on such a branch is stale or absent — so the push silently sends nothing, or fails. `HEAD:main` pushes whatever you actually committed to `main` regardless of the branch name you are sitting on.

Verify the push landed before reporting success:
```
git fetch origin main
git show origin/main:state/automation-log.md | tail -2
```
The summary line you just wrote must appear. If it does not, the push did not land — say so plainly.

(If push fails, retry once. If it still fails, log the git error prominently in the summary line — the run's Blotato-side work is done, but the state didn't persist and tomorrow's rotation will be off.)

**STEP 6 — Collect TikTok stats (best-effort, runs last)**

After posting and the state commit are complete, run:

```
pip install --quiet -r requirements.txt 2>&1 | tail -2 || true
python3 -c "import gspread, google.oauth2.service_account" 2>/dev/null \
  || pip install --quiet --upgrade --force-reinstall cryptography 2>&1 | tail -2 || true
python3 scripts/append_stats.py
```

**The install lines are required, not optional.** Each scheduled run starts in a
fresh container: `gspread` and `google-auth` are not preinstalled. Some images
also ship a Debian-packaged `cryptography` whose Rust bindings raise
`pyo3_runtime.PanicException` when `google-auth` imports them — the second line
detects that and repairs it. Its uninstall step may print an error
("RECORD file not found ... installed by debian"); that is expected and
harmless. Without these lines the script logs a STATS failure and collects
nothing.

It scrapes TikTok's public embed endpoints for follower counts, total likes and
per-post engagement across the 10 accounts, then writes them into the HPH
Operations Tracker — one row per account per day in `GrowthDaily`, and an upsert
keyed on TikTok Post ID in `PostLog`. It skips @angelagiles29, dedupes on
(Date, Account), and freezes a post's "Views (24h)" once a genuine 24-hour
number has been captured.

**This step is best-effort and its outcome does not affect the run.** The script
wraps collection, auth and each tab's write separately, logs failures to
`state/automation-log.md` with a `STATS:` prefix, and always exits 0. A stats
failure must never change what was posted, whether state was committed, or how
the run is reported. Do not retry it and do not treat a STATS error as a run
failure.

Requires `gspread` and `google-auth` (see `requirements.txt`) and the service
account credentials. If they are unavailable, the script logs and exits cleanly.

**ABSOLUTE RULES:**
- Read from `state/` (repo-root-relative), NOT from any Mac path.
- Past-slot skip guard is active — no clustering.
- Per-account mode drives slide 5 + caption CTA.
- 5 slides. GPT generation (`scripts/gpt_pilot.py`) by default; the Blotato Prominent Text template (`image`+`text` schema) only for fallback rows.
- mediaUrls = FULL array for carousels.
- `isAiGenerated=true` always.
- Schedule (never publish immediately).
- `get_visual_status` parameter is `id`.
- Commit state changes to git at end of every run.

**COST MODEL (from 2026-09-14 — GPT generation for all accounts):**

| | per post | per day (10) | per 30 days |
|---|---|---|---|
| OpenAI gpt-image-1-mini, medium quality (measured 2026-09-09 → 09-13: 1,584 output tokens per image, $0.0128/image) | ~$0.064 | ~$0.64 | ~$19 |
| Blotato — GPT rows | 0 credits | 0 | 0 |
| Blotato — fallback row (observed ~14 credits/slide since 2026-08-21) | ~70 credits (~$0.42) | — | — |

Cache hits reduce OpenAI spend only when the same (recipe, account) pair recurs, which the rotation does roughly every 110 days — so budget the full figure. `GPT_PILOT_QUALITY=low` would cut OpenAI cost to roughly a third (408 tokens/image) at visibly lower detail; Angela chose medium after the pilot.

**OpenAI account settings must allow this:** the monthly spend limit needs to be at least ~$30 and the prepaid balance kept above ~$20, or generation starts failing with a billing error and every row falls back to Blotato at ~70 credits each — which the ~5,000 monthly grant covers for only ~7 days. Set `OPENAI_MONTHLY_BUDGET_USD` on the Routine environment to the same figure as the OpenAI limit so STEP 4.9 warns at 80% and on an over-budget projection. `state/gpt-pilot-spend.json` is the running ledger.

Blotato's Creator plan grant (~5,000/month, lands ~7th) now funds fallbacks only. At ~70 per fallback post that is ~70 posts a month of headroom, so top-ups should be rare. There is no Blotato credit alert any more; STEP 4.9 reports OpenAI billing refusals, budget drift and missed slots instead, and the fallback balance is shown in that message.

History: the old 28-post, 6-slide Blotato day cost ~1,176 credits (~35,000/month, 7× the cap) and silently truncated runs; the 2026-08-11 cut to 10 × 5 slides brought it to a measured ~350/day, which had drifted to ~700/day (~14/slide) by late August. The GPT pilot on @postworkout.plate (2026-09-10 → 09-13) then ran 4/4 days with no fallbacks at $0.064/post, which is why generation moved to GPT for every account.

If `insufficient-credits` hits a fallback row, log it and continue; do NOT retry.
