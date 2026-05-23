# Deferred backlog plan — 2026-05-23 (R-F848)

**Status:** scoped, not in flight. This document captures everything
needed for a future session (Claude or operator) to pick up each item
cleanly without re-doing the discovery work.

**Why this file exists:** the session that shipped R-F794-F847 also
identified ~25 R-numbers reserved by earlier sessions but never
shipped. The 5-7 trivial ones (R-F829/F830/F831/F695/F794) were
closed in that same session. The 9 entries below need real planning,
either because they're multi-day feature engineering or because they
need operator/domain context that wasn't available.

**Phase-gate reminder:** per `CLAUDE.md` §1, **NO Phase B+ work
ships while a Phase A gate is open**. As of 2026-05-23, gates #3 / #5
(partial) / #7 are still open. The two big clusters below
(R-F618-F625 dialogue, R-F626-F633 DDGoal) are Phase B/C+. They MUST
NOT be picked up until either:
  1. All Phase A gates close, OR
  2. Operator says: "I understand Phase A gate #X is open. Override
     anyway."

---

## Quick reference

| R# | Class | Effort | Phase | Section |
|---|---|---|---|---|
| R-F596 | Tooling | ~1h | A-allowed | §1 |
| R-F817 | Operational | ~30m | A-allowed | §2 |
| R-F816 | Operational | ~30m (one-shot) | A-allowed | §3 |
| R-F695 (rest) | Operational | ~1h | A-allowed | §4 |
| R-F675 | Anti-wedge | ~2-3h | A-allowed | §5 |
| R-F827 | Cost guard | ~2-3h | A-allowed | §6 |
| R-F658 | DD enhancement | ~2-3h | B (out-of-phase) | §7 |
| R-F637 | New feature | ~4-6h | B/C (out-of-phase) | §8 |
| R-F618-F625 | Dialogue cluster (8) | 30-50h | B (out-of-phase) | §9 |
| R-F626-F633 | DDGoal cluster (8) | 40-60h | B/C (out-of-phase) | §10 |

Total deferred load: roughly **80-130 hours** of structured work.
Several sessions over 1-2 weeks.

---

## §1 — R-F596 · WEEKLY-CORE-META capability audit task

**Goal:** ARIA self-audits her own core mastery weekly. Pass-rate per
mastery tag (see `memory/aria_core_mastery_topics.md` — 10 tags)
trends published to the dashboard. Drift > 5% week-over-week tags an
OPPORTUNITY gap (R-F826) for ARIA-Coder to fix.

**Scope:**
- `aria_service/autonomous/tasks.yaml` — new entry `WEEKLY-CORE-META`
  with cron `0 9 * * mon` (Mon 09:00 UTC), `enabled: false` per
  conservative default
- `aria_service/intel/eval_runner.py` — extend export to per-tag breakdown
- `aria_service/intel/eval_golden_seed.py` — ensure golden seeds carry
  mastery-tag labels for filtering
- Redis key `crucix:meta_audit:weekly:{YYYY-WW}` — JSON of `{tag: pass_rate}`
- Dashboard panel (later — separate R-number)

**Acceptance:**
- Manual `POST /api/aria/autonomous/run-now/WEEKLY-CORE-META`
  produces a per-tag report
- Week-over-week diff > 5% creates an OPPORTUNITY gap via the
  existing R-F826 extractor

**Why deferred:** the cron-task wiring is trivial; the per-tag eval
extension needs care to not double-count or mis-bucket the golden
seeds. Don't ship without running the eval suite both before and
after to confirm no regressions.

---

## §2 — R-F817 · Expand R-F687 garbage domain filter

**Goal:** Reduce the recurring "alphabetical sweep" pollution that
trips the brain_hook breaker.

**Current state:** R-F687/F689/F691 maintain
`_GARBAGE_COMMON_NOUN_LABELS` in `aria_service/crawler/on_demand.py`.
R-F692 added algorithmic detection (`^[a-z]{3,10}$` at tier-4 +
sector=discovered) so the static list is mostly defense-in-depth.

**Scope:**
- Pull fly logs for the last 7 days
- Grep for `crawl sweep skipped %d auto-registered garbage domain(s)`
- Identify any pattern that escaped both the static list AND the regex
- Add NEW common-noun labels with date + log-source comment

**Acceptance:**
- Zero new "auto-registered garbage" log lines in 7d window after
  deploy
- All existing tests pass (no false positive on legitimate brands)

**Why deferred:** without observed NEW pollution, expansion is
premature. Re-check fly logs after 7 days; if zero hits, mark
"obsolete — algorithmic detection sufficient" and close.

---

## §3 — R-F816 · Trigger fresh adversarial run

**Goal:** Run the R-F80 prompt-injection suite + R-F59 social-eng
suite against the live aria-intel service and publish results to
mastery dashboard.

**Scope:** Operational, not code. Single shell invocation:
```
python scripts/train/eval_aria_llm.py \
  --target https://aria-intel.fly.dev/api/aria \
  --model deepseek-chat \
  --skip-defence-dd \
  --out /tmp/adv_$(date +%Y%m%d).json
```

**Acceptance:** Report file generated, adversarial pass-rate ≥ 0.80.

**Why deferred:** needs operator to confirm timing (one-shot, ~5-10 min
of live traffic) and to handle cost cap implications. Could fire ~80
LLM calls.

---

## §4 — R-F695 (remaining two parts)

**Goal:** Close the parts of R-F695 the 2026-05-23 session left open:

### 4a · Autonomous status data shape

`GET /api/aria/autonomous/status` — assessment said the data shape
needs straightening. Without concrete failure cases (consumer X
expected field Y), I can't know what to change. Steps for future
session:
1. Run the endpoint, capture response
2. Find ALL consumers: `grep -r "autonomous/status" --include="*.{mjs,html,py}"`
3. Diff response keys vs caller expectations
4. Add missing fields with defaults

### 4b · Cost projection

`GET /api/aria/cost/monthly/status` — needs a projection field
estimating month-end burn at current rate. Logic:
```
days_remaining = days_in_month - day_of_month + 1
projected_total = current_burn + (current_burn / day_of_month) * days_remaining
```
Add to response as `projected_month_end_usd` and `burn_per_day_usd`.

**Acceptance:** projection visible in `/api/aria/cost/monthly/status`,
no regression in existing consumers.

**Why deferred:** 4a needs consumer audit (10-20 grep targets); 4b is
straight-forward but should ship with 4a so the cost panel update is
a single PR.

---

## §5 — R-F675 · Absorb backpressure semaphore

**Goal:** Cap concurrent `brain_hook.absorb()` calls to N (target: 4-6)
so the absorb storm during cold-start (R-F845 already raised the
startup delay to 180s but didn't fix the storm itself) doesn't
saturate the event loop.

**Scope:**
- `aria_service/intel/brain_hook.py` — add module-level
  `asyncio.Semaphore(int(os.getenv("ARIA_ABSORB_CONCURRENCY", "4")))`
- Wrap every absorb dispatch path:
  - `absorb(module=, signal=, ...)` — primary
  - `absorb_silent(...)` — secondary
- Critical: do NOT add the semaphore at the call site (would require
  touching ~30 callers); add it inside the absorb function so all
  callers benefit automatically.
- Verify: pytest a synthetic 50-parallel-absorb scenario and confirm
  no more than 4 land in `_inner` at once

**Acceptance:**
- Unit test: 50 parallel absorbs serialize to N concurrent (sem cap)
- Live: aria-intel cold-start no longer flaps `1 critical` on Fly
  healthcheck
- No regression on absorb total rate (semaphore queues, doesn't drop)

**Why deferred:** touches the absorb critical path — every chat /
sweep / autonomous task hits it. Needs careful capability test
before ship.

---

## §6 — R-F827 · Cost-monitor per-task budget gate (P0 #1 from earlier audit)

**Goal:** Each autonomous task carries a per-run budget. Before
firing, cost_monitor checks `task_budget_remaining(task_id)` and
refuses if a single task has burned > $X this month.

**Scope:**
- `aria_service/autonomous/safety.py` — `can_task_run()` already
  checks the global cap. Add a per-task cap check.
- `aria_service/autonomous/tasks.yaml` — add `budget_usd_per_month`
  field per task (optional, defaults to global cap / N)
- `aria_service/intel/cost_monitor.py` — extend
  `record_llm_call(task_id=, cost_usd=)` with per-task rollup at
  key `crucix:cost:task:{task_id}:{YYYY-MM}`
- Surface in `/api/aria/cost/monthly/status` as `by_task: {id: usd}`

**Acceptance:**
- Setting `budget_usd_per_month: 5` on a test task → after $5 of
  spend in current month, the task returns `skipped:per_task_cap`
- Existing global cap behaviour unchanged

**Why deferred:** medium effort but straightforward. Schedule for
the next session that touches `safety.py`.

---

## §7 — R-F658 · Wire UBO walker into dd_orchestrator network layer

**Goal:** `dd_orchestrator` Layer 4 (network/ownership) should
recursively walk UBO chains using
`aria_service/intel/extract_ubo_chain.py` (which exists but is
unwired).

**Scope:**
- Read `extract_ubo_chain` signature + return shape
- In `dd_orchestrator/layer_4_network.py` (or wherever Layer 4 lives),
  add a call after the initial entity lookup
- Surface walked entities as `ubo_chain: [{name, jurisdiction, pct}, ...]`
  in the report
- Capability test: synthetic DD with a known UBO chain (e.g. an LLC
  with 3 levels of holding companies) — report must enumerate all 3

**Acceptance:** A DD report on a known multi-layer-UBO target lists
each layer with ownership %. Existing layer outputs unchanged.

**Why deferred:** Layer 4 is the DD orchestrator's bread-and-butter
output. Mis-wiring it would corrupt every DD report. Needs a
capability test with a real (or realistic synthetic) chain before
shipping. **Phase B work** per §1 phase gate.

---

## §8 — R-F637 · Pre-meeting cultural coaching slash-command

**Goal:** Operator types `/meeting-coach <country> <topic>` (in
chat / Telegram / WhatsApp) → ARIA returns a 1-page brief on
cultural communication norms relevant to the meeting.

**Scope:**
- New route `POST /api/aria/coach/meeting` returning structured
  brief (greeting style, gift norms, time-keeping, business-card
  protocol, dietary, language-switch cues)
- Knowledge source: existing
  `aria_service/intel/cultural_intelligence.py` (if it exists) plus
  RAG retrieval over country-specific etiquette corpus
- Slash-command wiring in `lib/telegram/` + `lib/whatsapp/`
- Chat UI: button on `aria.html` to insert the slash-command

**Acceptance:**
- `/meeting-coach Saudi M&A meeting` returns ≥6 specific tips
  cited to at least 2 sources
- Test: regression on 5 sample countries

**Why deferred:** New feature, needs:
1. Decision on which corpus to use (build new vs reuse existing)
2. Decision on which surfaces (Telegram + WhatsApp + chat UI vs just one)
3. UX design for the chat-UI button
4. Operator review of sample outputs for cultural accuracy
**Phase B/C feature** — Phase A gates must close first.

---

## §9 — R-F618-F625 · Dialogue-act cluster (8 R-numbers)

**Goal:** Transform ARIA from "chat responder" into "proactive
dialogue partner" — she opens conversations, chases unanswered
questions, dedups repeated explanations, calibrates her own
initiative level per user.

**This is feature engineering, not a fix.** Treat as a Phase B mini-product.

### Suggested implementation order (8 items)

| # | R# | Purpose | Effort | Depends on |
|---|---|---|---|---|
| 1 | R-F623 | Channel-agnostic `GoalContext(user_id, goal_id)` SQLite store | 4-6h | — |
| 2 | R-F618 | Per-user open-question tracking + chase logic | 5-8h | R-F623 |
| 3 | R-F619 | Recency window dedup (don't re-explain X within Y days) | 3-5h | R-F623 |
| 4 | R-F620 | Initiative-level picker 0..4 from GoalContext + UserModel | 4-6h | R-F623 + UserModel exists |
| 5 | R-F621 | 5 new dialogue-act handlers (CHALLENGE/GAP/NUDGE/OPTION/PRE-MORTEM) | 8-12h | R-F620 |
| 6 | R-F622 | Per-act post-scan constitution check (R-F557 stream-parity) | 3-5h | R-F621 |
| 7 | R-F624 | DAILY-CONVO-PROMPT autonomous task (one push per user per day) | 4-6h | R-F620 + R-F623 |
| 8 | R-F625 | Dialogue metrics dashboard + weekly drift detection | 4-6h | R-F624 |

**Schemas to design upfront:**
```python
class GoalContext:
    user_id: str
    goal_id: str
    title: str
    domain: str  # mastery tag
    opened_at: datetime
    last_engaged: datetime
    open_questions: list[OpenQuestion]  # → R-F618
    state: Literal["active", "paused", "resolved", "abandoned"]
    initiative_level: int  # 0-4, populated by R-F620

class OpenQuestion:
    id: str
    text: str
    asked_at: datetime
    user_answered: bool
    chase_count: int  # 0-5; R-F629's 5-stage ladder lives in DDGoal cluster
```

**Cross-cluster integration:** dialogue acts CHALLENGE / GAP need to
read DDGoal AskItems (R-F627). Build R-F623 first so both clusters
share the SQLite store.

**Acceptance gate for the whole cluster:**
- 1 design-partner user uses it for 1 week
- ARIA correctly chases ≥1 unanswered question per goal per week
- No double-explanation of the same concept within 7d
- All 5 new dialogue acts fire at least once each
- Constitution check catches any LLM regression (zero unredacted PII
  in proactive pushes)

**Why deferred:** 30-50h of structured work. Phase B. Needs:
- Operator buy-in on the conceptual model
- Schema review (the SQLite store will outlive the implementation)
- Design-partner volunteer

---

## §10 — R-F626-F633 · DDGoal cluster (8 R-numbers)

**Goal:** ARIA owns the long-tail of "things we need to figure out
about this target". Every DD report has gaps; each gap becomes an
AskItem with a routing target (operator vs contact vs source). The
chase scheduler then keeps grinding asynchronously until either the
gap closes or operator marks it accepted-as-residual.

**Suggested implementation order (8 items)**

| # | R# | Purpose | Effort | Depends on |
|---|---|---|---|---|
| 1 | R-F626 | `DDGoal` + `AskItem` schema + SQLite store | 5-8h | — (or R-F623 if shared) |
| 2 | R-F627 | DD suggestion engine — translate DD report gaps → AskItems | 6-10h | R-F626 + dd_orchestrator |
| 3 | R-F628 | AskItem routing via UserModel `domain_strengths` | 4-6h | R-F627 + UserModel |
| 4 | R-F631 | Cross-DD contact ledger — reuse verified contacts | 4-6h | R-F626 |
| 5 | R-F629 | Chase scheduler autonomous task + 5-stage ladder | 6-10h | R-F628 + R-F631 |
| 6 | R-F630 | Resolution absorber — re-run affected layers on answer | 5-8h | R-F629 + dd_orchestrator |
| 7 | R-F632 | Operator-accept-residual flow + audit log | 3-5h | R-F626 |
| 8 | R-F633 | DD chase metrics dashboard | 4-6h | R-F629 + R-F632 |

**5-stage chase ladder (R-F629):**
```
Stage 1: silent retry (re-query existing sources)
Stage 2: ARIA self-research (web_search + brain_hook)
Stage 3: queue for contact (R-F631 — known counterparty in same domain)
Stage 4: operator notification (low-priority WhatsApp / Telegram)
Stage 5: accept-as-residual (operator decision; R-F632)
```
Each stage has a configurable wait (e.g. 6h / 24h / 3d / 7d / forever).

**Cross-cluster integration with §9:**
- Dialogue acts (R-F621) consume AskItems (R-F626) to phrase CHALLENGE / GAP
- DAILY-CONVO-PROMPT (R-F624) pulls the top 3 AskItems for operator
- AskItem `routed_to=operator` triggers a dialogue-act event

**Acceptance gate for the whole cluster:**
- 1 live DD generates ≥3 AskItems
- Each AskItem routes to a sensible target per its domain
- Stage 2 self-research closes ≥30% of AskItems within 48h
- Stage 5 accept-as-residual produces an audit log entry on every use
- Dashboard shows: gaps per DD / time-to-close per stage / contact-ledger hits

**Why deferred:** 40-60h of structured work. Phase B/C. Same
operator buy-in + schema review prerequisites as the dialogue cluster.

---

## Sequencing recommendation

If/when Phase A gates close and operator green-lights Phase B:

**Wave 1 (Phase A, can ship anytime):**
1. R-F817 (15-30 min check after 7d log review)
2. R-F695 remaining (1h)
3. R-F596 (1h)
4. R-F675 absorb backpressure (2-3h — actually closes a known wedge symptom)
5. R-F827 per-task budget gate (2-3h)
6. R-F816 adversarial run (one-shot, 30 min including review)

**Wave 1 total: ~8-10 hours** spread across 1-2 sessions. Closes
6 of 9 deferred items, ~10% of total backlog by item count, ~5% by
effort.

**Wave 2 (Phase B+ — needs Phase A closure or operator override):**
7. R-F658 UBO walker (2-3h)
8. R-F637 cultural coaching (4-6h)

**Wave 2 total: ~7-9 hours.** Two more items, both Phase B
enhancements.

**Wave 3 (Phase B+ — major feature engineering):**
9. R-F618-F625 dialogue cluster — **start with R-F623** (the shared
   GoalContext schema) and stop. Take that to operator for review
   before building the other 7.
10. R-F626-F633 DDGoal cluster — same: **start with R-F626** schema,
    review, then build.

**Wave 3 total: 70-110 hours.** Plan for 3-5 separate sessions, with
operator review between each.

---

## Cross-cutting prerequisites (do these BEFORE any of Wave 3)

1. **UserModel** must exist and have `domain_strengths` field
   (R-F620 + R-F628 both depend on it). Audit current state in
   `aria_service/intel/user_model.py` (if it exists) or design it.

2. **R-F623 + R-F626 must share their SQLite store OR explicitly
   federate.** The schemas touch (`GoalContext` ↔ `DDGoal`,
   `OpenQuestion` ↔ `AskItem`). Decide upfront:
   - Single store with foreign keys (cleaner), OR
   - Two stores with a join key (`(user_id, target_entity_id)`)

3. **Constitution check pattern (R-F622) must align with R-F557
   stream-parity.** Both clusters do post-output scrubbing; share the
   scrubber so we don't double-implement.

4. **Operator dialogue capacity test.** Before R-F624's daily push
   ships, operator must confirm they have the bandwidth to engage —
   otherwise the chase ladder fills the queue with unanswered items
   and the metrics dashboard looks bad. Single design-partner
   trial first.

---

## Maintenance — what to do with this file

- **When a R-number ships:** delete its section here and add a one-line
  entry to the session R-tracker
- **When operator changes priorities:** update §Sequencing recommendation
- **When Phase A gates close:** re-classify all "Phase B/C" entries
  and consider promoting Wave 2 work
- **Quarterly:** prune stale entries; any R-number > 6 months old in
  this doc should be re-justified or closed-as-obsolete

This doc supersedes informal "deferred" mentions in commit messages
and session pickup notes. If something isn't here, treat it as ready
to pick up. If it IS here, treat it as planned-but-not-yet-actioned.
