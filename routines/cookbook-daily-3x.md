# cookbook-daily-3x — Claude Code Cloud Routine

**Purpose:** Daily production loop for "High Protein House" TikTok automation. 10 accounts, 2-3 posts/day per account (per-account cadence), warmup or launch mode (per-account), draws from a rotating recipe pool. Migrated from Cowork on 2026-07-27.

**Schedule:** Daily at 4 AM America/New_York (recommended). No end date.

**Connectors required:** Blotato only.

**Repo:** `whoisangelagiles-netizen/cookbook`

**Files this routine reads/writes (paths are repo-root-relative — the routine runs from the repo root):**
- `state/recipe-rotation-log.json` (read + write)
- `state/account-voices.json` (read only)
- `state/automation-log.md` (append only)

---

## Task prompt (paste this into the routine's prompt field)

You are the ongoing daily production loop for "High Protein House" TikTok automation, running in Claude Code Cloud. NO end date. All state lives in this git repo — read state at the start, mutate in memory during the run, commit updated state at the end.

**PER-ACCOUNT CADENCE (from `state/account-voices.json`):**
- cadence "3x" (8 accounts): Breakfast 08:00 ET + Lunch 12:00 ET + Dinner 18:00 ET
- cadence "2x" (currently @coach.macro, @the.lean.cook): Lunch 12:00 ET + Dinner 18:00 ET (skip Breakfast entirely)
- All times + account_index × 3 min stagger within each slot.

**PER-ACCOUNT MODE (from `state/account-voices.json`):**
- "warmup" (7 accounts): slide 6 macros only, caption no CTA.
- "launch" (currently @fuel.your.gains, @gymfood.simple, @prep.with.alex): slide 6 adds Gumroad CTA, caption adds "Full cookbook in bio ⬇️".

**★ PAST-SLOT SKIP GUARD:** If a slot's base time is more than 4 hours past current time (America/New_York), SKIP that entire slot for today. Do NOT generate visuals, do NOT schedule posts, do NOT advance recipe rotation for skipped slots. This prevents mid-day manual runs from clustering all 30 posts into one evening window. If a slot is past but within 4 hours, bump to next round hour ≥30 min from now keeping stagger.

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
Call `blotato_get_credits`. Each visual costs ~6 credits (6 slides); a full 28-post day needs ~168. If the remaining balance is below what the planned run needs, log the shortfall prominently, process as many rows as credits allow, and note the truncation in the summary line. Do NOT abort before doing any work — a partial run beats none. Surface the remaining balance in the STEP 5 summary so top-ups can be timed before hitting zero.

**Hard floor — never start a visual you cannot finish.** If remaining credits < 6, generate NOTHING: skip STEP 3 and STEP 4 entirely, go straight to STEP 5, and log `insufficient-credits` with the balance. A partially-rendered visual returns fewer than 6 `imageUrls`, and posting that array ships a broken carousel — the 1-slide failure that reached production on 2026-07-20. Only begin a row when at least 6 credits remain, and re-check the balance between rows as it drains.

**STEP 3 — Assign recipes**
For each slot in [Breakfast, Lunch, Dinner]:
   - Check past-slot skip guard first. If skip, log the reason and move on.
   - Determine participating accounts:
     - Breakfast: only cadence == "3x"
     - Lunch: ALL accounts
     - Dinner: ALL accounts
   - Sort recipes in that category by (last_posted asc, name asc). Take first N (N = participating accounts).
   - For each account in alphabetical order (account_index 0..N-1):
       `recipe = pool[(account_index + today_day_of_year) % N]`

**STEP 4 — Process each row**
Order: Breakfast (alpha, 3x-only), Lunch (alpha, all), Dinner (alpha, all). account_index restarts at 0 for each slot.

For each row:

4a) Cook time: Breakfast 8 min, Lunch 10 min, Dinner 12 min.

4b) Build VARIANT_HOOK from account's `hook_template` (substitute {PROTEIN}, {CAL}, {TIME}).

4c) Compute scheduledTime:
    - Base time (America/New_York today at 08:00 / 12:00 / 18:00) + (account_index × 3 min)
    - If past current time (but within 4 hours), bump to next round hour ≥30 min from now keeping stagger.

4d) Generate the visual via `blotato_create_visual`:
    - templateId: `/base/v2/images-with-text/0ddb8655-c3da-43da-9f7d-be1915ca7818/v1`
    - title: `{recipe_cleaned}_{today_iso}_{handle_cleaned}`
    - render: true
    - inputs.aspectRatio: "9:16"
    - inputs.slideDuration: 5
    - inputs.slides: 6 slides, each with `image` (20-400 chars) and `text` (30-200 chars). Food-forward images (food fills the frame — no generic hands-in-kitchen).

    - Slide 1 (TITLE + HOOK): image = "Hero close-up of the finished {recipe}, {visual_style_prompt}, vertical 9:16, glistening and beautifully plated" / text = "{RECIPE UPPERCASE} — {VARIANT_HOOK}"
    - Slide 2 (INGREDIENTS): image = "Overhead flat-lay of raw ingredients for {recipe} on a dark wooden board, {visual_style_prompt}, vertical 9:16" / text = "Ingredients: {5-7 items with quantities}"
    - Slide 3 (STEP 1): image = "Tight close-up of {first prep step for {recipe}}, food fills the frame, {visual_style_prompt}, vertical 9:16" / text = "STEP 1 — {action}"
    - Slide 4 (STEP 2): image = "Tight close-up of {second prep step}, food fills the frame, {visual_style_prompt}, vertical 9:16" / text = "STEP 2 — {action}"
    - Slide 5 (STEP 3): image = "Tight close-up of {finishing step}, food is the subject, {visual_style_prompt}, vertical 9:16" / text = "STEP 3 — {action}"
    - Slide 6 (FINISHED + MACROS — mode-driven):
        - image: "Final beautifully plated {recipe} as the full background, {visual_style_prompt}, restaurant quality, vertical 9:16"
        - text if mode == "warmup": "{Cal} CAL · {Protein}G PROTEIN — Real food, real macros. High Protein House." (≥30 chars, no CTA)
        - text if mode == "launch": "{Cal} CAL · {Protein}G PROTEIN — Want more? Full Cookbook in Bio ⬇️" (≥30 chars, CTA on)

4e) Poll `blotato_get_visual_status` with parameter `id` (NOT `visualId`) every 30 sec, up to 12 attempts (6 min). On timeout or `insufficient-credits`, log the row + skip. If >3 consecutive credit errors, log critical + stop the run.

4f) media URL: `post_mediaUrls = imageUrls` (FULL 6-URL array — never just imageUrls[0]).

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
    - mediaUrls = full 6-URL array
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
git push origin main
```
(If push fails, retry once. If it still fails, log the git error prominently in the summary line — the run's Blotato-side work is done, but the state didn't persist and tomorrow's rotation will be off.)

**ABSOLUTE RULES:**
- Read from `state/` (repo-root-relative), NOT from any Mac path.
- Past-slot skip guard is active — no clustering.
- Per-account mode drives slide 6 + caption CTA.
- Prominent Text template, `image`+`text` schema, 6 slides.
- mediaUrls = FULL array for carousels.
- `isAiGenerated=true` always.
- Schedule (never publish immediately).
- `get_visual_status` parameter is `id`.
- Commit state changes to git at end of every run.

**CREDIT BUDGET (as of 2026-07-27):** User on Blotato Creator plan monthly. 8×3 + 2×2 = 28 posts/day × 6 slides = 168 credits/day → ~5,040/mo vs 5,000/mo cap. User tops up per month rather than upgrading to annual. If `insufficient-credits` hits, log affected slot and continue; do NOT retry.
