# ARIA-LLM as the non-DeepSeek general fallback — readiness, in dependency order

**Written 2026-08-01, from live state, not from `§16`** (which is stale on this and says
"NOT wired into live chain"). Measured on aria-intel:

```
ARIA_LLM_URL             = https://…proxy.runpod.net/v1     ← RunPod, scheduled pod
ARIA_LLM_MODEL           = aria-llm-v0.1
ARIA_LLM_CANARY_PCT      = 50
ARIA_LLM_PROMOTION_STAGE = shadow
ARIA_LLM_SHADOW          = 0            ← contradicts the line above
ARIA_LLM_MAX_MODEL_LEN   = 16384
```

She is **wired, two-track (R-F2410), warm-gated (R-F2686), at 50% canary** — holding the
chat head while general traffic stays on DeepSeek.

## Why this matters now

R-F3634 established that for a general chat call the reachable chain is
`deepseek → deepseek_backup` — **two entries, one vendor**. A vendor-side timeout takes
both, so failover cannot help by construction. That is what produced five
"every provider failed" pages between 14:40 and 19:47 on 2026-08-01.

ARIA is the right long-term escape from that exposure. She is not ready to be it today,
and the blocker is **not model quality**.

---

## The order is a dependency chain, not a priority list

Each item is unsafe to attempt before the one above it.

### 1. Always-on inference hosting — THE blocker

`ARIA_LLM_URL` is a RunPod proxy. Per §24 the pod runs only inside scheduled windows
(Tue SFT / Wed DPO / Thu eval) and the scheduler **force-stops** anything running
outside 09:00–18:00 UK without a work-claim. **She is offline most of the week.**

**A fallback must be MORE available than what it backs up.** Wiring a
sometimes-on endpoint into the general chain would make outages *worse*: every DeepSeek
timeout would then wait out a second dead hop before failing. It would read as added
redundancy and subtract availability — the exact illusion R-F3634 stopped reporting.

- **Decision required (operator, spend):** a cheap always-on inference GPU, A40/L40S
  class. §24 already anticipates this for the shadow phase — "serving should move to a
  cheaper inference GPU; A100 only on training days."
- **Cost frame:** the §24 standing approval covers the weekly train/eval cycle
  (~$8–18/wk). Always-on serving is a NEW, larger line and is explicitly not covered.
- **Do not proceed past this item without it.** Everything below is wasted if the
  endpoint is not reachable when DeepSeek fails — which is, by definition, unscheduled.

### 2. ~~Reconcile the two promotion flags~~ — DONE (R-F3636, extended R-F4299)

> **CORRECTED 2026-08-24. The text below this line used to say the two flags
> "disagree" and that "nothing reconciles them". BOTH WERE FALSE WHEN WRITTEN,
> and this file is the one a session opens to decide what to do next — so it kept
> sending people to re-fix something already fixed.** That is the doc-driven
> revert loop CLAUDE.md §17 records for the Brave/WA rule: the instruction, not
> the code, was the thing reverting the work.

**R-F3636 already reconciled them, and in the safe direction.** `_shadow()` is
DERIVED (`promotion_stage() == "shadow"`) and is never read independently. The
legacy `ARIA_LLM_SHADOW` is an INPUT to the stage: truthy forces `shadow`, and it
can never act as a bypass. That asymmetry is deliberate and load-bearing —
letting `STAGE=canary` win over an explicit `SHADOW=1` would START SERVING USERS
for anyone relying on the older flag to hold the model back. The conservative
flag wins; that is the only direction safe to get wrong.

Measured 2026-08-24 through the RUNNING server (`GET /api/aria/llm/shadow`), not
from a detached process:

```
promotion_stage       = shadow      shadow = True     shadow_env_override = False
sovereign_pod_serving = False       (policy: §24 schedule)
sovereign_warm        = False       (proof: probe — matches a live 404 on the endpoint)
shadow_stats          = samples: 0  (she has never actually shadowed a turn)
```

**What was genuinely still broken was the REPORT, not the reconciliation** — and
it is what this item was reaching for. The same payload published `canary_pct: 50`
with nothing saying it is INERT at `stage=shadow`. A reader sees 50 and concludes
half of chat is served. She serves none of it. This file's own author recorded
making exactly that misreading: *"I misread it that way myself before tracing line
312, which is the point: a config that needs a code trace to interpret is not a
config."*

**R-F4299 (C-253) fixed that**, by publishing consequences beside the knobs:
`serving_users` (one field answering this item's question directly),
`canary_pct_effective` (0 while shadowing, `canary_pct` at canary, 100 at serve or
under R-F93's `primary_all` escape hatch), `legacy_shadow_var_present`, and
`model` — which nothing had ever reported, so no surface could show that live
points at **`aria-llm-v0.1` while the only models with recorded 500-Q evals are
v0.2 and v0.4**. That drift was invisible because the field did not exist.

**DONE 2026-08-24 (R-F4300), operator-directed.** `ARIA_LLM_MODEL` was
`aria-llm-v0.1`, a version nothing has ever evaluated. Now `aria-llm-v0.4-dpo`.
Set with a plain `flyctl secrets set` — NOT `--stage`, which §17 records leaving
the process still reading the old value after a full deploy — and verified from
the RUNNING process (`env` inside the container plus `GET /api/aria/llm/shadow`
reporting `model = aria-llm-v0.4-dpo`), never from `flyctl secrets list`, whose
STATUS reads "Deployed" for a stale value too. The change is inert while
`serving_users` is False, which is what made it safe to apply now.

**Mind the naming trap when picking this id.** The eval runs labelled v0.5, v0.6
and v0.7 in their FILENAMES all serve under the id `aria-llm-v0.4-dpo`; "v0.7" is
a file label, not a model. Read `model` in the report, never the filename.

### 3. Prove it on the objective gate

> **CORRECTED 2026-08-24 (R-F4300) — SHE HAS ALREADY CLEARED DEEPSEEK ON THIS
> GATE, and this file did not know.** The measurement existed in
> `data/eval_reports/` since July and nothing here or in §16 referenced it, so the
> readiness case was being argued from a model two generations behind the best
> measured one.

Same 500-row `defence_dd` set, same schema, same judge:

| served model id | accuracy | p50 latency |
|---|---|---|
| `aria-llm-v0.2` | 0.154 | 50.6s |
| `aria-llm-v0.3` | 0.220 | — |
| `aria-llm-v0.4` | 0.272 – 0.308 | 13.3s |
| **`aria-llm-v0.4-dpo`** | **0.502** (251/500) | **4.7s** |
| `deepseek-chat` (baseline) | 0.336 | 1.6s |

**`aria-llm-v0.4-dpo` beats the DeepSeek baseline by 16.6 points** and is ~3x
faster than plain v0.4 — while still ~3x slower than DeepSeek. DPO is what did it:
v0.4 -> v0.4-dpo is 0.272 -> 0.502 on the same set. §16 names DPO as a
prerequisite alongside the eval; it has been done and it worked.

**One honest limit on that comparison.** The artifacts record `model`, `target`
and the 500-row `defence_dd` block, but NOT the grounding condition, so "grounded"
(the DPO runs) and "openbook" (the DeepSeek baseline) cannot be proven identical
from the files alone. Same set, same size, same schema is strong evidence, not
airtight evidence. **Re-run both under one recorded condition before promoting
past shadow** — which needs the pod, so it sits behind item 1.

**What this changes:** the blocker was never model quality, and now there is a
number saying so. The case for funding item 1 is no longer "she might one day be
good enough" — it is "the best measured sovereign model already outscores the
vendor she would back up, and cannot be reached because nothing is serving her."


The frozen 500-Q eval (Phase A gate #6, pinned hash `a07b6af760ad7f44`, count 500) is
the honesty bar before she serves general traffic unsupervised.

- Gate #6 is CLOSED and the set is pinned — so the eval is *runnable and meaningful*
  right now. Any edit to the golden set RE-OPENS it; re-pin after a deliberate revision.
- §16 also names DPO as a prerequisite alongside the eval.
- **Judge by the failure SET, not the score** — the repo's standing doctrine. A score
  that improves without new evidence is the false clean
  (`north_star_priority_order_2026_07_21`).

### 4. Route by context length

`ARIA_LLM_MAX_MODEL_LEN = 16384` against DeepSeek's much larger window. Long-context DD
and research calls **cannot** route to her and must not be attempted.

- **Fail CLOSED:** if the estimated prompt exceeds her window, she is not eligible —
  never truncate to fit. Truncating a DD prompt to reach a cheaper model is how a
  contract gets clipped mid-clause (the R-F3630 lineage).
- Eligibility belongs in the same place dispatch computes its order, so the health
  surface and the dispatcher cannot drift — the R-F3634 lesson.

---

## What "done" looks like

`general_vendor_depth >= 2` on the live chain, with ARIA reachable on the general path,
her window respected, and the promotion stage stating one unambiguous thing.

Until item 1 is funded, the honest position is: **the general path is one vendor, and
that is an accepted exposure rather than an oversight.** R-F3634 makes it visible so it
stays a decision instead of a surprise.
