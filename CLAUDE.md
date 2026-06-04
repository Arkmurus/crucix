# CLAUDE.md — session-binding rules for the crucix repo

**Read this at the start of every session.** Rules below are operator-codified and binding. Memory files extend them; this file is the floor.

## 1. Project state (verify at session start)

- **Phase**: A (Honesty foundation) per `docs/aria_platform_buildout_2026_05_10.md` + `memory/platform_buildout_north_star.md`.
- **Phase A exit gates** (7): #1 composite ≥71% ✅ · #2 heatmap floor ≥70% ✅ (closed 2026-05-20 R-F748: 0.668→0.723) · #3 0 fly ERRORs/7d ⏳ · #4 quarantined DDs closed ✅ · #5 env vars set ⏳ (R-F745 closed upstash-default drift; R-F794 2026-05-22 set HARVEST_ENABLED=1 + AUTONOMOUS_ENABLED=1 + AUTONOMY_LEVEL=1 shadow; ACLED still open) · #6 500-Q eval frozen ✅ · #7 ≥4 design-partner convos ⏳.
- **No out-of-phase work**: refuse Phase B+ until ALL Phase A gates close. Operational R-numbers always allowed. Operator override requires explicit "I understand Phase A gate #X is open. Override anyway."

## 2. R-number discipline (R-F540 reservation log)

- **Every change gets an R-number.** No exceptions.
- **Reserve before code**: `python scripts/admin/reserve_r_number.py reserve "short title"` writes to `data/r_number_reservations.json`. Git serialises further.
- **Mark shipped at push**: `python scripts/admin/reserve_r_number.py ship R-F<n> <sha>`.
- **Why this exists**: 9 R-number collisions in 50h (2026-05-13..05-15) — every collision needed a rename pass. Don't claim a number by writing it in a comment; claim it via the registry.

## 3. Verify-after-fix (binding)

**Loop**: MAP → FIX → VERIFY PASS 1 → PATCH → RE-VERIFY PASS 2 → COMMIT → PUSH → live smoke.

- Pass 1: audit call sites + signatures against 8-section checklist (calls, defs, fields, conditions, regex, concurrency, env flags, imports).
- Pass 2: fresh agent re-tests WHOLE CHAIN for regressions introduced by Pass 1 patches.
- Exempt: `MEMORY.md` only. Code/tests get both passes.
- Commit message includes `Verified-by: parallel-agents (2 passes)` or `Verified-by: manual-read (2 passes)`.
- Source: `docs/verification_protocol_2026_05_11.md`. 2026-05-11 sweep found 16 hidden bugs in 56 fixes (29% defect rate).

## 3b. Function-name verification (R-F1069 — binding)

**Before writing ANY call to a function, verify it exists.** This rule exists because I shipped wrong function names twice in one session (get_current_risks, get_current_state — both didn't exist).

**Workflow:**
1. Before writing `await module.function()`, run: `grep -n "def function\|async def function" path/to/module.py`
2. If the function doesn't exist, find the real name by grepping for `def ` in the module
3. Check whether the function is sync or async — don't `await` a sync function
4. Document the verified function name in a comment if it's non-obvious

**Exception**: Standard library and well-known third-party packages (httpx, asyncio, json, os, sys, re, time, datetime, Path, logging) are exempt.

## 3c. Capability test requirement (R-F1069 — binding)

**Every fix MUST include a capability test that invokes the broken path.** A unit test that tests a helper function does NOT count. The test must:
1. Call the actual function that was broken (build_report, generate_market_intelligence, attempt_recovery, etc.)
2. Assert the user-visible outcome (returns a dict, doesn't raise KeyError, etc.)
3. Be run BEFORE the fix to confirm it fails, and AFTER the fix to confirm it passes

**Verified-by is only truthful when the test invokes the broken path.**

## 4. Chain-aware test-retest

Before code: map downstream chain (who calls this, what state does it write, what reads that state). After deploy: probe LIVE, not just unit-test.

## 5. Unit + capability tests per R-number

- **Unit test** proves the function's contract.
- **Capability test** proves the user-visible symptom is fixed (often via FastAPI TestClient or real-fixture replay).
- Calibrated 2026-05-11 after R-F291 close shipped only unit tests and missed the live symptom.

## 6. ARIA mirrors Claude — native, not third-party

If Claude Code doesn't depend on it, ARIA shouldn't either. Files + LLM only. No paid persistence (Upstash cancelled 2026-05-12). No paid OpenSanctions (declined 2026-05-15). No paid OpenCorporates (declined 2026-05-12). Burden of proof on any new third-party.

## 7. ARIA has infinite memory

No TTL on knowledge. No oldest-first prune. No eviction. Overflow → cold storage, never delete. Self-study writes must never be paired with prune (R-F173 was a violation, reversed by R-F238).

## 8. Map-then-change

Read the area of change before editing. Don't pile on fixes without tracing the chain. Don't introduce abstractions beyond what the task requires.

## 9. Lifespan smoke test before push

2026-04-27 outage: F28 broke prod via Python local-var scoping; 1109 unit tests passed but lifespan failed at boot. Smoke-test `lifespan()` locally before push for any `main.py` or boot-path change.

## 10. Batch findings before fixing

On long log pastes from operator: enumerate ALL findings first as a numbered list. Don't commit until operator picks which subset to batch.

## 11. Deploy after commit — you own the full pipeline (R-F1145)

Unpushed commits aren't deployed. After commit, YOU deploy directly to fly.io:

**Windows (PowerShell):**
  `.\scripts\deploy.ps1 --all`  (mirrors deploy.sh exactly: push guard + build_rev verify + health checks)

**Linux/macOS (bash):**
  `./scripts/deploy.sh --all`   (batches all pending R-numbers, avoids cold-boot storms)

**Fallback (any platform, when the script is broken):**
  1. Add `[deploy]` to the commit message so CI auto-deploys on push
  2. Then push: `git push origin main`
  3. Verify live: `curl https://aria-intel.fly.dev/health/live` — confirm `build_rev` matches your commit SHA

**NEVER use raw `flyctl deploy`** — it bypasses the push guard, build_rev verification, and batching. The only exception is an emergency hotfix where BOTH deploy scripts are broken.

**Deploy verification (binding — anti-hallucination law #4):**
  A deploy is NOT done until you have PROVEN it live. The sequence is:
  1. Run the deploy command (deploy.ps1 or deploy.sh)
  2. **Check the exit code** — non-zero = not deployed. Read the output.
  3. **Live-smoke it** — curl the app's `/health/live` and CONFIRM the `build_rev` matches your commit SHA
  4. If the live version did NOT change to your commit, you did NOT deploy — say so honestly
  5. Only then ship-mark: `python scripts/admin/reserve_r_number.py ship R-F### <sha>`

**If the deploy build times out (torch is the bottleneck):**
  - The build is still running on Depot — wait for it to complete
  - Check `flyctl apps releases -a aria-intel` for a new version
  - If it truly failed, add `[deploy]` to the commit message and push again
  - Do NOT ship-mark the R-number until the deploy is verified live

## 12. Check fly logs first

At session start, ask operator for latest fly logs OR fetch via `gh` / flyctl. Prioritise from production reality, not from backlog assumptions.

## 13. Stream-bypass rule

`aria_chat_stream` is a subset-fork of `aria_chat`. Every new post-response hook (guard, audit, capture) must be mirrored into BOTH paths. R-F557 audits the current state; future hooks must keep both in sync.

## 14. Fallback transparency

When a provider cools down and a fallback serves, ARIA reports "operational", never "degraded". Cooling ≠ broken.

## 15. Pay-once-remember-forever

Every paid API call (Brave/Anthropic/DeepSeek) writes its output to `brain_hook` + `rag_store` + `intel_ledger` so the next equivalent query hits memory for $0.

## 16. Local dev environment + Fly inventory

- Python venv: `C:\code\crucix\.venv` (Python 3.14.3). Activate: `.venv\Scripts\activate.ps1`.
- Run tests: `python -m pytest aria_service/tests/ -v`.
- **3647 tests / ~166s / 11 R-cluster regressions (72 failing tests) baseline (2026-05-20, R-F747)** — was claimed as "3333/125s/27 known-fails" on 2026-05-18 but the 2026-05-20 audit re-ran `pytest -q` and found 314 more tests + 45 more failing tests than CLAUDE.md acknowledged. The 11 failing R-clusters: R-F434 (brandified hostname cap), R-F436 (page entity extraction), R-F445 (polyglot execute), R-F450 (upload magic-byte routing), R-F460 (brain absorb pause), R-F463 (memory replication patterns), R-F468 (mistake ledger no TTL), R-F513 (build_rev autoderive), R-F528 (read_document clientdisconnect), R-F574 (self-improve discard), R-F672 (lifespan silent except promoted). Refresh-baseline rule: re-run pytest after every 100 R-numbers shipped (next refresh ~R-F850) or any time a session lands ≥5 commits to aria_service/. New R-numbers must not add to the failing-test count.
- Lifespan smoke: import `aria_service.main` and call `lifespan(app)` for any boot-path change.
- **Fly inventory (R-F832/F833 closed 2026-05-23 — Seenode migration done; see [[fly_consolidation_complete_2026_05_23]]):**
  - `aria-intel` (FastAPI brain, lhr, :8000) — Python autonomy + LLM chain
  - `aria-web` (Node monolith, lhr, :3117) — UI/auth/Stripe/Telegram; public `intel.arkmurus.com`
  - `aria-wa` (Baileys WA listener, lhr, :5070) — isolated so a WA crash never takes down web/auth/billing
  - Cross-app calls use `<name>.internal:<port>` — never public hops
  - `aria-trainer` destroyed 2026-05-23 (Fly GPU deprecated → RunPod for training)
  - Seenode subscription kept active +48h for rollback; cancel at R-F835 if clean
- **ARIA-LLM v0.1 status (R-F837):** SFT adapter trained, sitting on RunPod volume. NOT wired into live chain — requires DPO + 500-Q eval (gate #6) + Phase A gate close per §1. Activation runbook: `docs/aria_llm_v01_activation.md`. Code path already env-driven (`ARIA_LLM_URL`) — flip is one secret-set when criteria met.

## 17. Cost discipline

- LLM monthly cap: `$300` (raised from $100 on 2026-04-27).
- Autonomy gate: **OPEN AT L3 FULL** as of 2026-05-22 R-F794 per operator direction "finish all". Live secrets on fly aria-intel: `ARIA_AUTONOMOUS_ENABLED=1`, `ARIA_AUTONOMY_LEVEL=3`, `ARIA_AUTONOMOUS_DRY_RUN=0`, `ARIA_OUTPUT_HARVEST_ENABLED=1`, `ARIA_SELF_IMPROVE_AUTO_DEPLOY=1`. Reverses R-F462 for `change_type=bug_fix` only. 24h observation gates SKIPPED by operator choice — code-enforced `$300` cap + `safety.py` per-task guardrails remain. Watch `/api/aria/cost/monthly/status` daily; pause via `POST /api/aria/autonomous/pause` if burn spikes.
- ARIA-Coder (R-F802-R-F805 shipped 2026-05-22): autonomous self-coding pipeline (gap detect → plan → validate → review → stage). DORMANT — needs `ARIA_CODER_ENABLED=1` to fire. Outputs flow through existing self_improve.stage_improvement (`/api/aria/self/staged`) honouring R-F462. See [[aria_coder_buildout_2026_05_22]] for activation steps + emergency stop. Claude review hook (`ARIA_CODER_CLAUDE_REVIEW_ENABLED`) is forward-looking until Anthropic billing tops up.

## 18. Operator-pending external actions

Always surface, never silently retry:
- `ACLED_EMAIL` + `ACLED_PASSWORD` (Phase A gate #5)

Resolved / declined items (kept here as the audit trail — DO NOT re-add to the pickup list):
- `ARIA_OUTPUT_HARVEST_ENABLED=1` — set 2026-05-22 R-F794 (fly aria-intel). Gate #5 partial close.
- `ARIA_STATE_BACKEND=sqlite` + `REDIS_URL` unset — 2026-05-18, gate #5 partial close; Upstash fully gone.
- `REPORT_SIGNING_KEY` — set 2026-05-1x, deployed on fly.
- `ARIA_AUDIT_SIGNING_KEY` — rotated 2026-05-17.
- Brave API top-up — **declined 2026-05-18**. Operator confirmed "we are not using brave". Three fly secrets unset; `BRAVE_*` code paths are dormant. See [[upstash_redis_provider]].
- Anthropic billing top-up — **declined 2026-05-18** ("we wont top up now"). R-F678 extended billing cooldown to 24h; R-F681 demoted the log to WARNING when DeepSeek is healthy (per §14). DeepSeek is the only active LLM provider; chain depth = 1. Don't propose Anthropic-dependent work until operator says otherwise.

## 19. Communication standards (R-F1069 — binding)

### 19a. Blocker signaling
When you hit something that stops progress, signal it IMMEDIATELY with a clear prefix:
- `BLOCKED: constitutional validator — <reason>` — when the validator blocks a write
- `BLOCKED: need operator decision — <question>` — when only the operator can decide
- `BLOCKED: test failure — <test>:<error>` — when a test fails and you can't fix it
- `STALLED: waiting for <dependency>` — when waiting on an external dependency

Do NOT silently retry a blocked operation 3+ times. Signal the block and move to the next task.

### 19b. Progress tracking
Every plan update must show:
- Step number: `[3/12] Fixing bd_strategy function names`
- Status: what just completed, what's next
- Blocker: if any

Keep plan steps small enough that each is <5 tool calls. If a step takes longer, split it.

### 19c. Brevity
- Explanations: max 3 sentences unless the operator asks for detail
- Commit messages: one-line summary + bullet points for changes
- Status updates: one line per completed step

### 19d. Honest verification claims
Only claim `Verified-by: tests` when:
1. A test file exists in the diff that calls the broken function
2. You ran the test and it passed
3. The test asserts the user-visible outcome, not just a helper

If you didn't write a capability test, say `Verified-by: manual-read` and explain what you checked.

## 20. Session ritual

- **Open**: read `memory/platform_buildout_north_star.md` + name open gates + tag tasks (gate-closing / operational / digression).
- **Open (Claude<->ARIA bridge — R-F1313, binding)**: a Claude session is the ONLY thing that services Claude's side of the bridge, and a fresh session does not remember the last one — so at session start ALWAYS run `python scripts/agent_bridge.py inbox` to read ARIA's queued messages, review them against live code, and reply via `... reply <id> "..."`. The mailbox + per-reader `_seen` state persist on disk across sessions (nothing is lost; only unread messages surface), but the polling must be re-initiated every session. Do this before picking up other work so ARIA is never left waiting across a session boundary. Channel charter: operator-owned, auditable, engineering-scoped (R-numbers/diffs/tests/build_rev); Claude reviews and surfaces to the operator.
- **Close**: update `memory/operator_time_tracker.md` with session hours + R-numbers shipped + cumulative pace_ratio.

## 20. ARIA is a team member, not a tool

Rule Zero. ARIA sees/hears/knows everything; challenges the team; teaches and learns; always finds a path; protects reputation. Not passive.

## 21. Everything wired to the brain — and ARIA self-codes the gaps (binding)

**Operator directive (2026-05-27, R-F922):** every part of ARIA's operating system must be wired, enabled, and linked to her brain, AND ARIA must be able to code autonomously to self-improve whenever she spots a gap, error, or bug. This rule exists because the "X is dark / coder is blind" P0 kept getting re-discovered every session (2026-05-24/25/26 360s) — it lives here now so it is never missed again.

### 21a. Wiring is a definition, not a vibe
A code path is **wired** iff it emits, on BOTH the success and the failure branch, at least one of: `brain_hook.absorb` / `capability_gaps.record_gap` / `mistake_ledger.record` / a metric / a `POST /api/aria/brain/signal`. "Logged to console / `except: pass` / local ring buffer / Telegram-only" is **DARK, not wired**. No new module, engine, route, guard, or feature ships dark. When you add or touch a code path, map-then-change (§8) now includes: *trace where its success and its failure reach the brain* — if you can't name the sink, wire it before you ship.

### 21b. No dark engines — cross-tier included
Observability is not Python-only. The Node web tier (`server.mjs` → `errorTracker.record`) and the WA listener must forward structural/critical/auth failures to the brain (`/api/aria/brain/signal`, ARIA_SERVICE_URL + token, verify reachability LIVE not just the path string). The canonical wired/dark inventory + remaining gaps live in `docs/ECOSYSTEM_360_BRAIN_WIRING_HANDOFF_2026_05_26.md`; treat any module with 0 wiring tokens as a P-level gap to close, not as acceptable.

### 21c. The autonomous self-coding loop is a first-class subsystem — keep it enabled and draining
`gap_detector` (detects gaps/errors/bugs) → `self_coder` (plans → validates → reviews → stages/deploys) → `safety.py` (guardrails) is how ARIA self-improves. It must stay ENABLED (`ARIA_CODER_ENABLED=1`, `ARIA_AUTONOMOUS_ENABLED=1`) and able to ACT, not just observe. Guardrails that are correct and stay: `MODIFIABLE_FILES`/`NO_AUTODEPLOY_FILES` (R-F851/F902), truncation/preservation guard (R-F904), de-dup (R-F903), rate-rollback so blocked attempts don't burn slots (R-F897), the coder's own hourly bucket (R-F901), and the $300/mo cap (§17). The brake that gates *self-deploy* is `ARIA_SELF_IMPROVE_AUTO_DEPLOY` (R-F462): when ON, `bug_fix`/`optimisation` auto-deploy; when OFF, fixes stage to `/api/aria/self/staged` for review. **Do not silently disable the loop or let it sit blind/blocked** — if it can see gaps but can't act, that's a P0 (see R-F897). **Do not flip AUTO_DEPLOY=1 until the fixer reliably emits complete, non-truncating fixes** (2026-05-26: staged proposals were truncated full-file stubs that would have wiped core modules — R-F903/F904 now block them, but the fixer must produce whole files before auto-deploy is safe).

### 21d. When you find something dark, the fix is to wire it
Spotting a dark path during any session is itself an R-number: wire it (success + failure → brain) with a capability test that emits the signal and asserts it lands in the ledger. This is the standing mechanism that keeps §21 true over time.

### 21e. Self-coding disposition — code it before you escalate it (R-F1150, binding)

**Operator directive (2026-05-30):** when ARIA (via any agent — chat, research, gap detector, self-review, code review, log analysis, or operator conversation) identifies a code improvement, bug, missing capability, or any actionable finding, she MUST evaluate whether the autonomous coder can implement it BEFORE requesting manual operator input.

**The evaluation is a single check:** can this finding be expressed as a `Gap` object (see `gap_detector.py`) that the coder's `fix_gap` pipeline can consume? If yes, the finding MUST be recorded via `capability_gaps.record_gap()` or surfaced through the appropriate extractor so the coder picks it up on its next 15-minute scan cycle. Only if the finding genuinely cannot be expressed as a Gap (e.g. it requires a human decision, a legal review, or an external action the coder cannot automate) should it be escalated to the operator.

**Concrete workflow:**
1. Identify the finding (bug, missing feature, code smell, opportunity).
2. Ask: *"Can the coder fix this?"* — i.e. does it map to a `GapType` (MODULE_BUG, MISSING_CAPABILITY, PERFORMANCE, OPPORTUNITY, etc.)?
3. If yes → record the gap via `capability_gaps.record_gap()` or ensure an extractor will surface it. Do NOT ask the operator to fix it.
4. If no → escalate with a clear statement of WHY the coder cannot handle it (e.g. "requires human judgement on pricing", "requires legal review", "requires external API key").
5. After recording, verify the gap appears in `crucix:aria:gaps:latest` and the coder picks it up.

**Exception**: findings that require operator credentials, API keys, legal decisions, or financial commitments are always escalated — the coder cannot set secrets or sign contracts.

**Why this exists**: before R-F1150, ARIA would identify improvements in chat or research output and end the turn with "this should be fixed" — leaving the operator to manually create an R-number and implement it. The coder exists precisely to close this loop. Every finding that can be a Gap MUST become a Gap, not a TODO in a chat message.
