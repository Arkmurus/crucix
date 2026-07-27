# Session record — 2026-07-27 — R-F3265 … R-F3294

Claude session, run alongside a second Claude agent in the same working tree on
`main`. All work below is committed, pushed and live.

## Shipped (12 R-numbers, all verified live)

| R-number | What | Tier |
|---|---|---|
| R-F3265 | vetting reads real documents: PDF (text layer + conditional OCR), images, DOCX, `.eml` **including attachments** | intel |
| R-F3266 | pack-upgrade path — a case can move onto a newer PRODUCTION pack, recorded and forward-only | intel + web |
| R-F3267 | vetting stage tiles + coverage grid sizing | web |
| R-F3269 | vetting queue search, DD-library parity | web |
| R-F3274 | documents reach the career timeline; application form populates declared periods | intel + web |
| R-F3283 | no-AI-dash copy rule extended to 27 of 28 public pages | web |
| R-F3285 | AMBER had no colour — the class name lost its hyphen | web |
| R-F3287 | watchlist enrolment requires a human | intel |
| R-F3289 | SMTP reported "configured" while unable to authenticate | web |
| R-F3290 | R-F2750 guard pinned a caption R-F3225 replaced | tests |
| R-F3293 | `collab_bridge_drain` handler unreachable from `run_task` | intel |
| R-F3294 | three autonomous guards predating deliberate tightenings | tests |

Also: **R-F3282** registered the weekly-report fix that shipped unnumbered under
a stolen `R-F3243` label (commit `d98c2063`), with an alias note so a future
grep resolves.

## The three findings worth remembering

**1. AMBER (R-F3285) — a class name built by STRIPPING a separator.**
`sev.toLowerCase().replace(/[^a-z_]/g, '')` turns `AMBER-LIGHT` into
`amberlight`, while the stylesheet defines `.dd-pill.amber_light`. The rule
never matched. GREEN and RED are single words and matched fine, which is exactly
why it survived — the two loudest states looked right. The same line had been
copied to three more surfaces; `dashboard.html` had it with a worse symptom (its
map was keyed `'amber-light'` WITH the hyphen, so amber fell through to
`sc-badge-muted` and rendered **grey**, the colour reserved for having no
verdict). `vls-chain.html` was already correct — R-F2065 had found the same
thing in isolation years of commits earlier and fixed only that one surface.

**2. Watchlist cost (R-F3287) — the gate belongs on the INSERT, not the miss.**
R-F2401 made dedup per-owner, so an automatic call naming a *different* owner
falls THROUGH the dedup branch to the insert. Gating only the
"not-already-present" case would have left a hole exactly the width of a second
tenant. `add_to_watchlist` is the only writer of `WATCHLIST_KEY` — proven by
naming the five other writers and confirming each only mutates or removes.

**3. `collab_bridge_drain` (R-F3293) was masked, not working.** The handler
existed and the task named it, but it was absent from `run_task`'s dispatch
tuple, so every run answered "unsupported tool kind". Nobody noticed because
R-F1548 later added an independent 2-minute scheduler loop calling
`drain_for_aria()` directly. A masked failure surfaces when the thing masking it
is changed.

## Operator actions still outstanding

- **SMTP secret.** `EMAIL_USER` and `EMAIL_PASS` hold the *identical* value
  (digest `49bb8a67b557e235`) and take precedence over the correctly-distinct
  `ARIA_EMAIL_*` pair. Set `EMAIL_PASS` to the real password, or unset
  `EMAIL_USER`/`EMAIL_PASS`. Until then mail is in log-relay mode and says so.
- **Existing auto-enrolled watchlist entries** are not purged (removing a
  monitored entity is the user's call) but are now marked "added automatically
  by a past DD, not by you" so they can be pruned deliberately.

## Handed to the other agent

- **`test_rf1498_portal_requirements_email.py` hangs in isolation** (~60s+).
  Both tests call `email_portal_requirements_to_operator()`, which at
  `portal_registry.py:3016` calls `determine_and_drive_all()` — with
  `portal_ids=None` that drives every pending and `needs_operator` portal
  (R-F1716). A unit test about email composition is driving live portal
  registration. **This is why the full Python suite cannot complete on this
  machine**, which blocks §23 verification for everyone. `portal_registry.py` is
  their file (last touched `aa41fe4d`).
- **`deploy.ps1` exact-sha verification** reports `[FAIL]` on a *successful*
  deploy whenever the other agent ships past you. Should accept the live sha
  when `git merge-base --is-ancestor <mine> <live>` holds.

## Two-agents-one-tree notes

Both agents commit to the same local `main`. Consequences seen today: my
uncommitted edit to `test/page-copy-cleanup-rf3225.test.mjs` was swept into
*their* commit `2f918d50` (confirmed with `git log -S`, not assumed), and we
independently fixed the same guard under the same reserved number R-F3292.
Nothing was lost, but **verify authorship and commit contents rather than
inferring them**.

Verification discipline used throughout: failure sets diffed against a clean
worktree at `origin/main` rather than compared as totals — the first baseline
went stale after 8 peer commits and briefly made three of their reds look like
mine.
