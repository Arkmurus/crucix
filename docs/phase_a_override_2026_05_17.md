# Phase A Override — 2026-05-17

**Closes audit action 6** (CLAUDE.md §1 override-phrase audit trail).

## What this document is

CLAUDE.md §1 requires that any work crossing the Phase A boundary into
Phase B carry an explicit operator override of the form:

> *"I understand Phase A gate #X is open. Override anyway."*

The R-F661, R-F662, R-F663 commits in the 2026-05-17 learning-framework
buildout did cross that boundary. Their commit messages say "explicit
operator override granted 2026-05-17" but do not quote the exact phrase.
The audit 2026-05-17 closing-session report flagged this as a process
gap. This doc backfills the audit trail.

## The override (verbatim)

During the 2026-05-17 session, the operator was presented with this
exact text in an AskUserQuestion option (Question: *"Phase B override
for R-F661 / R-F662 / R-F663?"*):

> **Option chosen**: "Yes — override granted, proceed with all three"
>
> Option description: "You're stating: 'I understand Phase A gates
> #2/#5/#6/#7 are open. Override anyway.' I then build R-F664 (py-fsrs)
> → R-F661 (reading queue) → R-F662 (controller, autonomous cron,
> LLM-free loop) → R-F663 (bookmarks). Each commit standalone, tested +
> re-tested, no cloud-LLM calls in the learning loop."

By selecting that option, the operator explicitly invoked the CLAUDE.md
§1 override formula:

`I understand Phase A gates #2/#5/#6/#7 are open. Override anyway.`

The four gates open at the moment of override were:

- **#2** Heatmap floor ≥ 70%
- **#5** All operator-pending env vars set
- **#6** 500-Q evaluation set v1 frozen
- **#7** ≥ 4 design-partner relationship conversations underway

Gate **#3** had been flipped to ✅ earlier in the same session
(verified live via `/api/aria/health/error-streak` reporting
`phase_a_gate_3_pass: true`, 7 consecutive clean days).

## R-numbers covered by this override

| R# | Phase | Commit |
|---|---|---|
| R-F661 | B | `43d5482` (failed-quiz → reading-list auto-enroll) |
| R-F662 | B | `91214af` (OSS-only learning controller) |
| R-F663 | B | `5dcd738` (controller bookmarks + 5-min debounce) |

R-F659 (DD input validation), R-F660 (completion metric), and R-F664
(py-fsrs scheduler) were classified Phase A in the same session and
did not require this override.

## Scope discipline

The override was issued for the three specific R-numbers above, in the
context of the OSS-only learning-framework buildout. It is **not** a
blanket Phase B unlock. Further Phase B work crossing into closed-gate
territory requires a fresh override per CLAUDE.md §1.

The autonomy gate (CLAUDE.md §17 / `memory/cost_cap_and_autonomy_gate`)
remains in force. The LEARNING-CYCLE cron defaults to OFF
(`ARIA_LEARNING_CONTROLLER_ENABLED=0`); the operator flipped it ON
in the same session via `flyctl secrets set
ARIA_LEARNING_CONTROLLER_ENABLED=1 -a aria-intel`.

## Re-verifying the override

If a future audit asks where the override lives, the canonical sources
in order of authority are:

1. The session transcript (durable in the user-facing conversation
   record — operator's `AskUserQuestion` response captures the choice).
2. This document.
3. The commit messages for R-F661 / R-F662 / R-F663 (referenced above).

Audit closed: R-F671.
