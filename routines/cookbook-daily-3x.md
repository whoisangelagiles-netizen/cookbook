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

Why: measured Blotato cost is ~7 credits/slide. The old 28-post, 6-slide day cost ~1,176 credits (~35,000/month against a ~5,000/month cap — 7× over, which is why runs silently truncated and Breakfast disappeared in June). The new shape is 10 × 35 = ~350 credits/day, ~10,500/month.

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

**STEP 2.5 — Credit preflight**
Call `blotato_get_credits`. **Billing is PER SLIDE: ~7 credits/slide. A 5-slide post costs ~35 credits.** Verified three times: 6.8, 6.9 and 7.0 credits/slide. A full 10-post day at 5 slides needs ~350 credits. If the remaining balance is below what the planned run needs, log the shortfall prominently, process as many rows as credits allow, and note the truncation in the summary line. Do NOT abort before doing any work — a partial run beats none. Surface the remaining balance in the STEP 5 summary so top-ups can be timed before hitting zero.

**Hard floor — never start a visual you cannot finish.** If remaining credits < 35, generate NOTHING: skip STEP 3 and STEP 4 entirely, go straight to STEP 5, and log `insufficient-credits` with the balance. A partially-rendered visual returns fewer than 5 `imageUrls`, and posting that array ships a broken carousel — the 1-slide failure that reached production on 2026-07-20. Only begin a row when at least 35 credits remain, and re-check the balance between rows as it drains.

**STEP 2.6 — Credit alert to Slack (#tech)**

Compute `needed = participating_accounts x 35` (a full day is ~350).

Post to Slack channel `#tech` (ID `C0ARUTE3PPC`) via `slack_send_message` in
either of these cases, and in no others — do not post on a healthy run:

**A. Cannot complete today (`balance < needed`)** — this is the "ran out" case.
**B. Fewer than 3 days of runway (`balance < needed x 3`)** — early warning, so
there is time to act before a day is actually lost.

Post at most ONE message per run. If both conditions hold, post the A version.

Before posting, size the top-up **around the monthly refresh** — do not buy
credits the plan is about to grant for free.

**Monthly refresh:** the Creator plan grants ~5,000 credits on about the **7th
of each month** (observed 2026-08-07: balance jumped 2,005 -> 7,005, exactly
+5,000). Blotato's API does not expose a renewal date — `blotato_get_user`
returns only plan and status — so this is inferred from one observation. If a
refresh lands on a different day, correct this line.

Sizing:
- `days_to_refresh` = days from today to the next 7th
- `need = days_to_refresh x daily_burn` (daily_burn = participating_accounts x 35)
- `quantity = clamp(round_up_to_1000(need - balance), 1000, 10000)`
- If `balance >= need`, the balance already reaches the refresh: do NOT generate
  a checkout link and do NOT post case B. Only case A still applies.

This matters. On 2026-08-22 the naive "restore 30 days" rule recommended 10,000
credits when only ~6,000 were needed to reach the Sept 7 refresh — about $24 of
credits that would have sat idle.

Then call `blotato_buy_credits` with that quantity. It returns a `checkoutUrl`
and **charges nothing** — a Stripe Checkout link the account owner must open and
complete. Safe to generate unprompted.
- The Blotato account is `whoisangelagiles@gmail.com`. Credits are
  non-transferable between accounts, so state the email in the message.

Message format:

```
:warning: *High Protein House — Blotato credits low*

Balance: *{balance}* credits
Today's run needs: *{needed}* ({n} posts x 35)
Runway: *{floor(balance/daily_burn)} day(s)* at current cadence
Next refresh: *{refresh_date}* (~5,000 credits, in {days_to_refresh} days)
Needed to reach refresh: *{need}*
{if A}: *Today's run cannot complete in full.* {scheduled}/{n} posts went out.

Recommended top-up: *{quantity}* credits (~${quantity * 0.006}) — sized to reach the refresh, not beyond it
Checkout: {checkoutUrl}

Account: whoisangelagiles@gmail.com (credits are non-transferable)
Link charges nothing until completed.
```

If Slack is unavailable or the call fails, log `SLACK-ERROR: <reason>` to
`state/automation-log.md` and continue. **A Slack failure must never block
posting or the state commit** — same rule the Sheets step had.

**Structural note to include when runway is short:** at 10 posts/day the
cadence costs ~10,500 credits per 30 days against a ~5,000/month grant — a
standing gap of ~5,500 credits (~$33) every cycle. Top-ups are not a one-off
fix; they are a recurring line item until the cadence, slide count or plan
changes. Say so plainly rather than implying a single purchase resolves it.

Requires the Slack connector on the Routine. If `slack_send_message` is not
available, log `SLACK-UNAVAILABLE` once and carry on.

**STEP 3 — Assign recipes**
There is ONE slot (18:00 ET) and ALL 10 accounts participate.

- Check the past-slot skip guard first. If 18:00 ET is more than 4h past, skip the day entirely: generate nothing, schedule nothing, advance no `last_posted`.
- **Draw from ALL categories.** Sort the ENTIRE recipe set — breakfast, lunch, dinner and snack together — by (last_posted asc, name asc). This is the full 110-recipe rotation, not dinner-only. The voice templates work for any category, and a wider pool means each recipe resurfaces far less often.
- Take the first 10 (N = 10 accounts).
- For each account in alphabetical order (account_index 0..9):
    `recipe = pool[(account_index + today_day_of_year) % N]`

**STEP 4 — Process each row**
Order: alphabetical by handle, account_index 0..9, all in the single 18:00 ET slot.

For each row:

4a) Cook time: use the recipe's own category to pick a sensible figure — breakfast 8 min, lunch 10 min, dinner 12 min, snack 5 min. This feeds {TIME} in the hook template.

4b) Build VARIANT_HOOK from account's `hook_template` (substitute {PROTEIN}, {CAL}, {TIME}).

4c) Compute scheduledTime:
    - Base time (America/New_York today at 18:00) + (account_index × 3 min)
    - If past current time (but within 4 hours), bump to next round hour ≥30 min from now keeping stagger.

4d) Generate the visual via `blotato_create_visual`:
    - templateId: `/base/v2/images-with-text/0ddb8655-c3da-43da-9f7d-be1915ca7818/v1`
    - title: `{recipe_cleaned}_{today_iso}_{handle_cleaned}`
    - render: true
    - inputs.aspectRatio: "9:16"
    - inputs.slideDuration: 5
    - inputs.slides: **5 slides** (verified 2026-08-11 — the template accepts 5 and returns exactly 5 `imageUrls`, billing 35 credits). Each slide has `image` (20-400 chars) and `text` (30-200 chars). Food-forward images (food fills the frame — no generic hands-in-kitchen).

    **LOCKED 5-SLIDE LAYOUT (changed 2026-08-11 from 6):**
    - Slide 1 (TITLE + HOOK): image = "Hero close-up of the finished {recipe}, {visual_style_prompt}, vertical 9:16, glistening and beautifully plated" / text = "{RECIPE UPPERCASE} — {VARIANT_HOOK}"
    - Slide 2 (INGREDIENTS): image = "Overhead flat-lay of raw ingredients for {recipe} on a dark wooden board, {visual_style_prompt}, vertical 9:16" / text = "Ingredients: {5-7 items with quantities}"
    - Slide 3 (STEPS 1-2 — combined): image = "Tight close-up of {first prep step for {recipe}}, food fills the frame, {visual_style_prompt}, vertical 9:16" / text = "STEPS 1-2 — {action one, then action two}"
    - Slide 4 (FINAL STEPS): image = "Tight close-up of {finishing step}, food is the subject, {visual_style_prompt}, vertical 9:16" / text = "STEP 3 — {finishing action}"
    - Slide 5 (FINISHED + MACROS — mode-driven, carries the CTA):
        - image: "Final beautifully plated {recipe} as the full background, {visual_style_prompt}, restaurant quality, vertical 9:16"
        - text if mode == "warmup": "{Cal} CAL · {Protein}G PROTEIN — Real food, real macros. High Protein House." (≥30 chars, no CTA)
        - text if mode == "launch": "{Cal} CAL · {Protein}G PROTEIN — Want more? Full Cookbook in Bio ⬇️" (≥30 chars, CTA on)

    The old 6-slide layout split prep across three slides (STEP 1 / STEP 2 / STEP 3). Slides 3 and 4 of that layout are now merged into slide 3. Do not emit 6 slides.

4e) Poll `blotato_get_visual_status` with parameter `id` (NOT `visualId`) every 30 sec, up to 12 attempts (6 min). On timeout or `insufficient-credits`, log the row + skip. If >3 consecutive credit errors, log critical + stop the run.

4f) media URL: `post_mediaUrls = imageUrls` (FULL 5-URL array — never just imageUrls[0]).

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
    - mediaUrls = full 5-URL array
    - scheduledTime = ISO 8601 with America/New_York offset
    - privacyLevel = "PUBLIC_TO_EVERYONE"
    - disabledComments = false, disabledDuet = false, disabledStitch = false
    - isBrandedContent = false, isYourBrand = true
    - isAiGenerated = true (REQUIRED — TikTok AI disclosure)
    - title = first 80 chars of VARIANT_HOOK

4i) On success, update the recipe's `last_posted` = today ISO in the in-memory rotation-log dict.

4j) On row failure, log and continue. On >3 consecutive credit/cap errors, log critical + stop.

**STEP 5 — Persist state (git commit + push)**
Write updated `state/recipe-rotation-log.json` with new `last_posted` values (only for recipes successfully scheduled — leave skipped recipes untouched so they surface first next run). Refresh `last_updated` field.

Append a one-line summary to `state/automation-log.md`:
```
- [{timestamp}] DAILY-3X (mixed cadence + mode): {connected}/{expected} accounts, cadences [{3x_count}×3x + {2x_count}×2x], slots-fired [{slots_fired}], slots-skipped-past [{slots_skipped}], {visuals} visuals, {posts_b}B + {posts_l}L + {posts_d}D scheduled ({posts_total} total). {errors} errors. Launch CTAs on: [handles]. Credits remaining: {credits_remaining}. {notes}
```

Then commit + push:
```
git add state/recipe-rotation-log.json state/automation-log.md
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
python3 scripts/append_stats.py
```

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
- Per-account mode drives slide 6 + caption CTA.
- Prominent Text template, `image`+`text` schema, 5 slides.
- mediaUrls = FULL array for carousels.
- `isAiGenerated=true` always.
- Schedule (never publish immediately).
- `get_visual_status` parameter is `id`.
- Commit state changes to git at end of every run.

**CREDIT BUDGET (measured 2026-08-11 — supersedes all earlier estimates):** Blotato Creator plan, ~5,000 credits/mo cap.

Billing is **per slide**, not per post. Measured 7.0 credits/slide (3,498 → 3,463 for a 5-slide render on 2026-08-11), consistent with 6.8 and 6.9 measured across two 28-post days.

| | credits |
|---|---|
| One slide | ~7 |
| One 5-slide post | ~35 |
| Full day, 10 posts | ~350 |
| 30-day cycle | ~10,500 |

That is ~2× the ~5,000/mo cap, so expect a recurring top-up of roughly 5,500 credits (~$33) per cycle. The earlier "6 credits per post / 168 per day" figure confused the daily SLIDE count (28 × 6 = 168) with the credit cost; the old 6-slide, 28-post day actually cost ~1,176/day (~35,000/month, 7× over cap), which is why runs silently truncated and Breakfast vanished in June.

If `insufficient-credits` hits, log the affected slot and continue; do NOT retry.
