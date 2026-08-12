# Routine setup — lessons learned the hard way

Written 2026-08-12 after six days of failures getting `cookbook-daily-3x` to run
unattended. Applies to any Claude Code Cloud Routine, not just this project.

---

## 1. Create Routines in the UI. Never via the API.

**This is the single most important rule here.**

A Routine created through `create_trigger` (the MCP/API path) prompted for
connector permission on every scheduled run and hung forever, because a
scheduled run has no human to approve it. It failed five consecutive days:
08-06, 08-07, 08-08, 08-09, 08-11.

Things that did NOT fix it:
- Setting the connector to "always allow" (the banner even says tools run
  without asking — it still prompted)
- Attaching the repository
- Changing the Permissions tab
- Rewriting the prompt
- Disabling and re-enabling

A Routine created **manually in the claude.ai UI**, with the same connector,
same repo and same settings, worked on its first scheduled run with no prompt.

Likely cause: the UI creation flow writes a permission/approval grant that
`create_trigger` does not. `update_trigger` cannot repair it — that API exposes
only `name`, `cron_expression`, `enabled`, `model`, `prompt` and `run_once_at`.
There is no permission field to set, which is why editing could never fix it.

Caveat: one success against five failures, and the Permissions tab was also
changed during manual creation, so "UI creation" and "correct grant" are not
fully separated. Treat the rule as strong, not proven.

**Corollary: treat a working Routine as read-only infrastructure.** Do not edit
it through the API to save a copy-paste. The downside (losing the only working
configuration, with no undo) dwarfs the upside.

## 2. Put the real spec in the repo, not in the Routine prompt.

Keep the Routine prompt thin and make it defer:

> `routines/<name>.md` on main is the authoritative spec. Read it first and
> follow it. Where this prompt and the spec disagree, the spec wins.

Why this matters:
- The spec can be changed with a commit — no risk to the Routine object.
- Prompt and spec drift apart otherwise. Ours did: a wrong credit figure lived
  in the prompt for weeks after the spec was corrected, and a category cap
  lived in the prompt but never reached the spec, producing a lopsided day.
- Anything expressible in the spec should live there. The prompt should carry
  only rules about how the *run itself* behaves (see §3).

## 3. Guards that earned their place

Each of these exists because its absence caused a real incident.

**Recompute the date immediately before every scheduling decision.** Not once at
session start. A run computed "today" at startup, its worker restarted, and ~31
hours of wall-clock passed before it created posts — it tried to schedule into
the past. Long-running sessions drift; assume it.

**Never schedule earlier than the current moment. Never "catch up" a missed
day.** A run rebuilt the previous day's plan at already-passed timestamps.

**Check for existing work before creating any.** Read what is already scheduled
and skip anything matching (time, target). One run created 23 duplicate posts.

**Never delete.** A run deleted 5 of its own correct posts while "cleaning up"
its duplicates. Deletion should require a human.

**Verify outputs against ground truth before persisting state.** A run
mistranscribed a batch and built 3 posts with wrong media (one had 1 of 5 images,
from the wrong recipe). It caught this by re-reading the source before commit and
repaired them. Without that step, three accounts ship broken content silently.

**Always write a log line and commit — even on an aborted run.** Two failures
went unnoticed for a day each because they left no trace. A run that logs
"aborted, reason X" is infinitely better than one that vanishes.

## 4. Push with `HEAD:main`, never `origin main`

Attaching a repo to a Routine sets an auto-generated outcome branch (e.g.
`claude/gallant-lovelace`). The session is not on `main`. `git push origin main`
pushes the *local* `main` ref, which is stale or absent there — so it silently
pushes nothing, or fails, while the outward-facing work has already happened.

```
git push origin HEAD:main
```

Then verify it landed:

```
git fetch origin main && git show origin/main:<file> | tail -2
```

A run that reports success on a push that did not land is how state silently
stops persisting.

## 5. Measure costs. Do not trust the estimate in your own spec.

Our spec claimed "~6 credits per visual, 168/day". Measured reality was ~7
credits **per slide** — the 168 was the daily *slide count* (28 posts × 6 slides),
mistaken for a credit cost. Actual burn was ~1,176/day, about 7× the plan cap.

The failure was invisible: an over-budget cadence does not error, it truncates.
Breakfast silently stopped firing in June and nobody knew why for two months.

Measure by taking a balance reading before and after a known unit of work, then
repeat at a different size to confirm the unit. We measured 6.8, 6.9 and 7.0
credits/slide across three runs, including one at a different slide count — that
third reading is what proved billing was per-slide and not per-post.

## 6. Turn notifications on

`push` and `email`. Ours were off, so total failures produced silence. The first
two were only discovered because someone happened to look.

## 7. Watch for rotation starvation

Selecting "least recently posted across all categories" put ten snack recipes in
one 6 PM slot, because no snack had ever been posted so the entire category
sorted first. Correct by the letter of the rule, wrong in practice.

Any least-recently-used selection over a pool with an untouched subset will
produce this. Cap per-category share (e.g. max 3 of 10 slots) so a starved
category cannot monopolise a day.

## 8. Never commit secrets, even briefly

A GCP service account key was committed to this **public** repo. Deleting the
file does not help — git history is public and permanent. The only fix is
rotating the key at the provider.

Pass credentials via environment variables (`GOOGLE_SA_KEY_B64`), keep a
`.gitignore` covering `secrets/`, `*.pem` and `.env*`, and make credential
loaders accept either raw JSON or base64 so a format mismatch cannot silently
break auth.

---

## Checklist for a new Routine

1. Create it **in the UI**
2. Attach the repository
3. Add only the connectors it needs
4. Set Permissions to run unattended
5. Turn on push + email notifications
6. Point the prompt at a spec file in the repo; keep the prompt to guards only
7. Include: recompute-the-date, no-past-scheduling, idempotency, no-deletes,
   verify-before-persist, always-log-and-commit
8. Use `git push origin HEAD:main` and verify the push landed
9. Measure real costs before trusting any budget figure
10. Let one run complete unattended before assuming it works
