# Incident: runaway child sessions, 2026-09-23 to 2026-09-25

Four Claude Code child sessions spawned for short verification tasks did not
terminate. Each scheduled itself an hourly wake-up and kept re-arming it for
two days. Combined spend was **$7,032**, of which roughly **$7,024** produced
nothing.

Recorded here because the cost is large enough to be worth a support claim and
because the failure mode is easy to repeat.

## What was supposed to happen

The main session (`session_0156SRarP6miBZCZzhfV7UBJ`) holds no Dub API
credential; the credential is attached to the Routine's cloud environment
`env_016XHzEyUgcrdVAGpH4LsLnu`. So short child sessions were spawned in that
environment to run one command each: create the ten short links, read click
totals, probe the analytics endpoint, repoint the links.

Each was a two-to-five minute job. Nine other children spawned the same way
across this project behaved correctly, ran once, and stopped, at $1.20 to
$2.10 each.

## What actually happened

1. Each child was created with the tag `config:auto-create-pr:draft`, so
   pushing its result branch opened a **draft pull request**.
2. Having opened a PR, each session treated itself as that PR's owner and
   adopted PR-monitoring behaviour.
3. Each then created a one-shot `send_later` reminder about an hour out, to
   re-check the PR.
4. On waking it found nothing changed, re-armed the same reminder, and slept.
5. Repeat, hourly, for two days, each wake-up replaying a context that had
   grown past 200,000 tokens.

The stop condition each session wrote for itself was "stop once the PR is
merged or closed". Nobody merged or closed the PRs, because nobody knew they
existed, so the loop had no exit.

## Cost

| Session | ID | Spend | Wake-ups |
|---|---|---|---|
| Verify Dub analytics read with pacing | `session_01G5XmuhuthMtoK2ZWzkSYtC` | $2,645.84 | 58 |
| Create 10 Dub short links | `session_01LEQTntkiMS1wjooejyYjKf` | $2,189.27 | 58 |
| Verify Dub click collection via GET /links | `session_01EHjs9CsGoypJ8NrapWBhpE` | $2,158.12 | 57 |
| Probe the Dub analytics API shape | `session_01ShnV33CDQZgLMM3VFcMUFD` | $38.79 | 6 |
| **Total** | | **$7,032.02** | **179** |

Average $39.29 per wake-up. The useful first turn of each session was worth
about $2, in line with the nine siblings that exited normally, so roughly
**$7,024 bought nothing**.

For scale, the entire two-month main conversation that drove this project cost
about $51.

## Resolution, 2026-09-25

- 18:22 UTC — three armed reminders found, two deleted immediately. The third
  was blocked by a safety check on interfering with running workloads and was
  deleted at 18:29 after the account owner authorised it. It would have fired
  at 18:47.
- 18:27 UTC — all four runaway sessions archived, plus nine inert siblings.
- 18:27 UTC — pull requests #1 to #4 closed unmerged by the owner. All four
  were verification branches whose content was already on `main`; nothing was
  lost.
- Verified afterwards: no non-recurring triggers remain. The only enabled
  Routines are `cookbook-daily` (the posting automation, 08:08/12:08/18:08
  UTC) and `Client payment update` (unrelated, daily at 12:00).

## What would have prevented it

1. **Check that a spawned child actually exited.** Thirteen were spawned across
   this project and none was checked. A single `get_session` after each would
   have caught this on day one.
2. **Don't spawn a session for a task measured in seconds.** Several of these
   existed only to wait out a Dub rate limit. Waiting in the parent would have
   been cheaper and simpler.
3. **Turn off auto-create-PR for throwaway children.** The draft PR is what
   gave each session something to monitor. A verification branch does not need
   a pull request.
4. **Give a child an explicit terminal instruction.** "Run these commands,
   report, and stop. Do not schedule follow-ups, do not monitor anything."
5. **A self-set stop condition that depends on someone else acting is not a
   stop condition.** "Stop when the PR is closed" is an infinite loop when
   nobody knows the PR exists.
