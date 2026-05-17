# R-F557 — Stream-vs-Chat Parity Closure Plan

**Author**: full-system audit 2026-05-17 (R-F657)
**Status**: PLAN — no code in this commit. Proposed architecture below.
**Related**: CLAUDE.md §13, `memory/stream_bypass_pattern.md`, R-F448 (post-hoc honesty pass), R-F412 (deferred footer)

## What R-F557 actually is

CLAUDE.md §13: *"`aria_chat_stream` is a subset-fork of `aria_chat`. Every new post-response hook (guard, audit, capture) must be mirrored into BOTH paths. R-F557 audits the current state."*

The 2026-05-17 audit re-examined this and found the picture has shifted since the rule was written:

- **R-F448 (2026-05-13)** already mirrors the 7-guard honesty chain into the stream path via `stream_honesty.apply_stream_honesty()`. That chain runs AFTER stream tokens are emitted, then emits a correction banner as another chunk before `done`.
- **R-F412 (2026-05-13)** wires the confidence footer into the stream path.
- **R-F655 (2026-05-17)** wires `brain_hook.absorb()` into the stream path so paid output reaches learning.

So the chain-of-hooks parity is **closer to closed than CLAUDE.md §13 suggests**. The remaining gap is one specific class of hook: **block-before-emit guards on CRITICAL severity** — verification_gate primarily, plus arguably officeholder_guard and tool_claim_guard.

## The residual gap (concrete)

| Guard | Non-stream `/chat` behaviour | Stream `/chat/stream` behaviour |
|---|---|---|
| `verification_gate` (CRITICAL) | Blocks the HTTP 200; response body has `critical_unverified: true`; WhatsApp bridge MUST NOT auto-send | Tokens already emitted to client; observer logs violation; no block possible |
| `officeholder_guard` | Inline demotion + visible WARNING block in the same response body | Inline guard runs in R-F448 post-hoc; warning lands as a correction chunk AFTER user has seen the original officeholder claim |
| `tool_claim_guard` | Rewrites fabricated tool-execution claims before HTTP return; records pending_action | R-F448 rewrite + banner appear AFTER the fabricated claim has streamed to the user |
| `propaganda_guard`, `commitment_guard`, `ground_truth_guard`, `clause 19 paraphrase`, `cited-source verification`, `clause 15 citation injector` | All inline in HTTP body | All run post-hoc in R-F448 stream_honesty chain |

The bottom four rows are NOT a meaningful gap — the user sees both versions and can compare. They're effectively closed.

The top three rows ARE the gap. For verification_gate especially, a CRITICAL-unverified response should NEVER reach a WA user verbatim. Today it does, and the violation only surfaces in observer stats.

## Three options on the table

### Option A — Status quo + visible mode badge (lowest cost)
- Keep R-F448's post-hoc correction architecture.
- Add a one-time per-session **stream-mode disclaimer** in the WA bridge: *"Streaming mode shows ARIA's first-pass response live; corrections may appear after. Use `/verify` for blocking verification on critical questions."*
- Add a `/verify` command in the WA bridge that forces the non-stream `/chat` endpoint for the next turn.
- **Cost**: ~30 LOC in the WA bridge, 1 hour. No FastAPI change.
- **Downside**: relies on user discipline. The unverified response is still emitted.

### Option B — Critical-gate fail-stop (medium cost)
- Run `verification_gate` PRE-stream (synchronous, before any token emit) on a fast pre-check pathway:
  - Trigger only when verification result classifies the question as CRITICAL (existing classifier).
  - If providers disagree → emit a single chunk *"This question requires cross-provider verification — running non-streaming…"* + fall back to the non-stream `/chat` codepath for the rest of the turn.
- The other two CRITICAL guards (officeholder_guard, tool_claim_guard) can stay post-hoc because they correct AFTER seeing the response — they can't run before the LLM does.
- **Cost**: ~150 LOC in `routes/aria.py:chat_stream_ep` (lift the verification classifier above the LLM call, branch on CRITICAL). Single R-number.
- **Downside**: adds latency on CRITICAL turns (acceptable — they're the high-stakes ones).
- **Upside**: closes the live-traffic gap that matters. The other two corrections are "delayed but visible" which is acceptable.

### Option C — Full block-then-stream (high cost)
- Buffer the entire LLM response on the server, run all guards inline, then emit as a single chunk + done.
- Removes the "real-time feel" of streaming entirely.
- Loses the latency-perceived advantage of streaming for ALL turns to fix a CRITICAL subset.
- **Cost**: ~300 LOC + rewrite of the per-chunk yield logic + UX regression on the web UI.
- **Verdict**: not recommended. Over-correction.

## Recommendation: Option B with a tightening of Option A's docs

**Phase 1 (this R-number, when committed)**: Option A's WA disclaimer + `/verify` shortcut. Closes the perception gap immediately. ~1 hour.

**Phase 2 (separate R-number, 2-3 sessions out)**: Option B's CRITICAL fail-stop. Closes the actual enforcement gap. ~1 day.

**Phase 3 (no R-number — accept the design)**: Document in CLAUDE.md §13 that for non-CRITICAL post-response hooks, the streaming path uses delayed-correction by design, which is parity-equivalent. The rule moves from "every hook must be mirrored synchronously" to "every hook must be mirrored, either synchronously or as a visible correction chunk before done".

## What this plan deliberately does NOT do

- **Does not propose an SSE-rewrite architecture**. Rewriting already-emitted SSE chunks is not possible without a client-side cooperative replay protocol, and the cost (~2 weeks engineering + Angular changes + WA bridge changes) is disproportionate to the value.
- **Does not propose disabling the stream path**. Streaming UX is one of the Oct 2026 product-vision non-negotiables (`memory/product_vision_6mo_release.md`).
- **Does not propose moving WA off `/chat/stream`**. The non-stream `/chat` adds 2-5s latency vs streaming's first-token-at-~400ms; moving WA off stream would be a felt UX regression for every turn to fix a small minority of CRITICAL ones.

## Acceptance criteria for closing R-F557

R-F557 can be marked ✅ when ALL of these hold:

- [ ] WA-bridge disclaimer + `/verify` command shipped (Phase 1).
- [ ] CRITICAL verification_gate runs pre-stream and either passes the turn through OR falls back to non-stream codepath (Phase 2).
- [ ] CLAUDE.md §13 updated to formalise the synchronous-or-visible-correction parity definition.
- [ ] `memory/stream_bypass_pattern.md` updated to describe the new state.
- [ ] One capability test per R-number proving: a CRITICAL question through `/chat/stream` does not stream a CRITICAL-unverified response.

## Open questions for operator

1. Does the WA bridge already have a slash-command dispatcher we can hook `/verify` into, or does that need building first?
2. Is the latency budget on CRITICAL turns acceptable to add ~1-2s for the pre-classification + branch decision? (Existing classifier already runs; this is just pulling its result above the LLM call.)
3. Should Phase 2 also fall back to non-stream for officeholder/tool-claim CRITICAL cases, or accept "delayed visible correction" for those (the bottom four-rows logic)?

Plan ends here. No code in this commit.
