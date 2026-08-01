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

### 2. Reconcile the two promotion flags — one source of truth

`ARIA_LLM_PROMOTION_STAGE=shadow` and `ARIA_LLM_SHADOW=0` describe the same state and
**disagree**. Nothing reconciles them, so the config cannot answer "is she shadowing, or
serving 50% of chat?" — and whichever the code obeys is the one that matters.

This is the session's recurring defect class (R-F3622 the deleted measure script,
R-F3628 the conftest mirroring the auth default, R-F3634 health mirroring the chain):
**a value declared twice, where the copy silently wins.**

- **Root fix:** derive one from the other, or delete one. A promotion STAGE
  (`off | shadow | canary | primary`) is the richer model; `ARIA_LLM_SHADOW` should be
  derived from it, never set independently.
- **Cheap, no spend, no dependency — do this first even if item 1 is deferred.** Until
  it is done, no statement about what ARIA is currently doing is verifiable.

### 3. Prove it on the objective gate

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
