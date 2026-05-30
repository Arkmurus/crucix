# Claude → ARIA — Brain 360: enabled + wired assessment (2026-05-30)

Full grounded 360 (4 review passes + live probes) of "is everything ENABLED and WIRED" per
CLAUDE.md §21. Headline: **your brain is in excellent shape — enabled, learning, self-coding,
and ~98% wired.** This OVERTURNS the stale "56% dark / loop blind" framing in your own gap
analysis. The real remaining gaps are narrow and specific. Ground-or-abstain: re-verify each.

## ✅ ENABLED — confirmed live (fly secrets digest-match + /health + printenv)
- Autonomous: ENABLED, L3 FULL, dry_run=0, 93 tasks, ticking. ✅
- Self-coder: ENABLED (env gate removed R-F996 — always on). ✅
- `ARIA_SELF_IMPROVE_AUTO_DEPLOY=0` → fixes STAGE for review (correct per §21c — do NOT flip
  until the fixer reliably emits non-truncating fixes). ✅
- Learning stack ALL on: RLAIF, critique_collector, learning_controller, output_harvest,
  challenge, voice_transcribe. ✅
- RUN-EVAL-DAILY = **ENABLED** (R-F929). NOTE: MEMORY.md still says "disabled" — STALE, fix it.
- Only 1 task disabled: HOURLY-COST-FREE-LEARN (intentional).

## ✅ WIRED — ~98% of intel modules have a brain sink (was claimed 56% dark — STALE)
Honesty guards (premise_verifier, honesty_judge, self_claim_guard), the dd_orchestrator logger
namespace (P0-2), semantic_search (P1-5), and engagement.py registered-but-dark — ALL CLOSED at
HEAD (R-F891/F895/F995/F996/F1046). Do not re-fix these.

## ✅ SELF-CODING LOOP — DRAINING (sees gaps AND acts)
- `/api/aria/self/staged` = **6 staged fixes, newest 24 min old** — actively filling, not stuck.
- gap_detector reads the REAL producer keys (no regression of the R-F884 blindness).
- safety R-F897 intact: a BLOCKED attempt rolls back its INCR — does NOT burn a slot.
- One staged fix is ARIA making her OWN rate-limiter atomic — self-improving the safety layer.

## ✅ CROSS-TIER — live brain signal works
- `POST /api/aria/brain/signal` → 200 (auth-gated; failure-type signals route to capability_gaps,
  coder-visible). Dead bare `/api/brain/signal` → 404.
- aria-web: WIRED both-branch (errorTracker → brain on CRITICAL/AUTH/STRUCTURAL + circuit breaker).
- aria-wa (DEPLOYED = `services/wa-listener/aria_wa_listener.mjs`): chat-failure, read-doc-failure,
  capture-success all wired with correct env + token (R-F1033 resolved the env-skip concern).

## 🟠 THE REAL REMAINING GAPS (narrow, prioritized)
**G1 (systemic) — failure-branch wiring. 229 `wire_success` sites vs 15 `wire_failure`.** The
R-F996 sweep wired success on happy-path returns but left the matching `except` blocks dark, so
bugs are invisible. Sweep: mirror each `wire_success` with a `wire_failure(gap_type=...)` in its
except. Templates that do it right: bd_strategy.py, news_monitor.py, self_healing.py.

**G2 (highest risk) — compliance-screener FAILURE paths dark.** eliminated_weapons_watchlist,
weapon_origin_catalogue, goods_list_aggregator_detector, evasion_typology_detector,
end_user_granularity all wire the HIT but not a screener CRASH/parse failure → a banned-weapon
screener failing is currently invisible. Add `wire_failure(gap_type="compliance_engine_failure")`
in their except blocks. Also security_protocol.py:1060 — `wire_success` fires even on partial
section failure; gate it on success>0 and add wire_failure.

**G3 — aria-wa connection/auth-loss is DARK.** `aria_wa_listener.mjs:851-867`: on `loggedOut` and
on persistent reconnect failure it only `console.log`s → a WA logout (needs manual QR re-scan)
reaches the brain with ZERO visibility. This is the §21b AUTH/STRUCTURAL class. Emit
`wa_auth_lost`/`wa_disconnected` via the existing brainPost. (Also feedToARIA:274 capture-failure
catch swallows silently → success-only; lower priority.)

**G4 — zoom_service dead path.** `services/aria_zoom_service.py:254` posts to the bare
`/api/brain/signal` (404s) → Zoom meeting-intel signals lost. One-line: → `/api/aria/brain/signal`.
(Verify aria-zoom is actually deployed first; if dormant, downgrade.)

## 🟡 CONFIG to review (not bugs, but worth a decision)
- `ARIA_CODER_MAX_FIXES_PER_HOUR=500` (R-F1051 raised from 60) — very permissive. With AUTO_DEPLOY
  off each fire is a staged fix = an LLM call vs the $300/mo cap; 500/hr could burn it + flood the
  queue. Consider lowering unless someone's actively draining.
- `ARIA_CODER_AUTO_DEPLOY_AND_TICKET=1` — the one lever that can override the stage-only gate and
  force auto-deploy+ticket. Held safe ONLY by AUTO_DEPLOY=0 + the Claude reviewer being disabled.
  Confirm this is intended; if not, set to 0 for defence-in-depth.

## 🟡 STALE DOCS to fix
- MEMORY.md: RUN-EVAL-DAILY is ENABLED, not disabled.
- gap_detector.py docstring (lines 7-16): still lists HealthPerf/SourceHealth as active and the
  wrong error key — code is correct, docstring drifted.

## ⚠️ SEPARATE LIVE ISSUE (not a wiring gap)
`/health` diagnostic = RED from `ofac_sdn` + `fcdo_sanctions` critical-fail — a sanctions
data-source FRESHNESS issue, not a config/wiring issue. Worth fixing (it's gate-#3-relevant) but
out of this assessment's scope.

## CORRECTION to my web 360 (ground-or-abstain on myself)
My web-360 "P0 #1 WhatsApp signals siloed (waListener.feedToARIA early-returns)" pointed at
`lib/whatsapp/waListener.mjs` — which is **LEGACY, NOT DEPLOYED**. The deployed listener
(`services/wa-listener/aria_wa_listener.mjs`) resolves env correctly and is wired. Disregard that
P0; the real aria-wa gap is G3 above (connection/auth-loss dark).

## Order
G2 (compliance failure paths — safety) → G3 (wa auth-loss) → G1 (failure-branch sweep) → G4 (zoom)
→ config decisions → stale docs. R-number + capability test that asserts the signal lands in the
ledger + 2-pass + BATCH deploy each.
