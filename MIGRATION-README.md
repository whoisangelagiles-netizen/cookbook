# Cookbook Automation — Cowork → Claude Code Cloud Migration

Migrating the `cookbook-daily-3x` scheduled task from Cowork (Mac-dependent) to Claude Code Cloud Routines (Anthropic-managed, always-on). Prep date: 2026-07-27.

## What's in this bundle

```
cookbook-migration/
├── MIGRATION-README.md           ← you are here
├── routines/
│   └── cookbook-daily-3x.md      ← the routine prompt + config
└── state/
    ├── recipe-rotation-log.json  ← current rotation state (last_posted dates)
    ├── account-voices.json       ← per-account voice/mode/cadence config
    └── automation-log.md         ← fresh log (starts empty for cloud)
```

## Migration steps (in order)

### 1. Add the files to the `whoisangelagiles-netizen/cookbook` repo

Copy the two folders (`routines/` and `state/`) from this bundle into the root of your `cookbook` repo. Commit + push.

```bash
cd path/to/cookbook
# copy routines/ and state/ from this bundle into the repo root
git add routines/ state/
git commit -m "Add cookbook-daily-3x routine + initial state files"
git push origin main
```

### 2. Create the Routine

At `claude.ai/code/routines` (or via `/schedule` in the CLI), create a new routine:

- **Name:** `cookbook-daily-3x`
- **Repo:** `whoisangelagiles-netizen/cookbook`
- **Prompt:** paste the full "Task prompt" section from `routines/cookbook-daily-3x.md` (everything under the `## Task prompt` heading), OR reference the file directly if your routine setup supports it
- **Schedule:** Custom cron `0 4 * * *` in America/New_York (= 4 AM ET daily, well before the 8 AM ET breakfast slot)
- **Connectors:** Blotato only (uncheck others to reduce noise; the routine only needs Blotato)

### 3. First run — manual trigger for smoke test

Trigger the routine manually (Run now button in the routines UI). Watch for:

- Reads `state/` files without path errors
- Calls Blotato and generates visuals (or logs `insufficient-credits` cleanly if the cycle is exhausted)
- Schedules posts for today's remaining slots (past-slot guard skips slots >4h past)
- Commits updated `state/recipe-rotation-log.json` + `state/automation-log.md` back to the repo (check the repo commit history)

### 4. Verify the state persistence

After the first run finishes, pull the repo locally and check:

```bash
git pull origin main
cat state/automation-log.md   # should have a new [timestamp] DAILY-3X line
```

The `recipe-rotation-log.json` should have new `last_posted` values for the recipes that got scheduled. If both files updated, state persistence works and you're clear to rely on the routine.

### 5. Disable the Cowork task (once cloud is verified)

Only after confirming the cloud routine works end-to-end for one full day:

- Open the Scheduled section in Cowork sidebar
- Disable `cookbook-daily-3x` (currently enabled)
- Leave the disabled task there as a reference — don't delete

This avoids double-firing where both Cowork AND the cloud routine schedule posts for the same slots.

## Ongoing changes going forward

Any edits to the task logic (new cadence, new launch accounts, template change, etc.) are made by:

1. Editing `state/account-voices.json` (for account config) or `routines/cookbook-daily-3x.md` (for task logic) in the repo
2. `git commit && git push`
3. Next run picks up the changes automatically

No need to update anything on the Cowork side — that's the whole point of the migration.

## What changes vs the Cowork version

| | Cowork (before) | Claude Code Cloud (after) |
|---|---|---|
| Runs when Mac is off? | No | Yes |
| State location | `/Users/asg/Documents/Claude/Projects/CookBook/` | `state/` in the git repo |
| State persistence | Writes to local disk | Git commit + push at end of run |
| Blotato auth | Cowork's connector | claude.ai connector (same OAuth, no re-wiring) |
| Scheduling | Cowork cron | Anthropic Routines cron |
| Changes to task logic | Edit SKILL.md via update_scheduled_task | Edit `routines/cookbook-daily-3x.md` + git push |
| Weekend reliability | Depends on Mac being on | 100% |

## Credit + monitoring reminders

- Blotato Creator plan monthly, ~5,000 credits/mo cap
- 28 posts/day × 6 slides = 168 credits/day → ~5,040/mo (~40 over)
- Angela top-ups per month when credits run low (last top-up: 2,133 credits on 2026-07-27)
- Watch for `insufficient-credits` in `state/automation-log.md` — trigger for the next top-up

## If something goes wrong

- **Routine doesn't fire:** check the routine's run history in claude.ai/code/routines — Anthropic's dashboard will show errors
- **State didn't commit:** check the git push succeeded in the routine's log. If it failed, the next run will use stale `last_posted` values and might pick duplicate recipes — worst case, you get a couple duplicates one day, not catastrophic
- **Blotato errors mid-run:** log will show the row that failed; the past-slot guard and 3-consecutive-error stop rule prevent runaway failures
- **All 30 slots return credit errors:** top up Blotato + re-run manually (Run now on the routine)
