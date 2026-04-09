# ARIA Layer 3 — Autonomous Research Engine

**Status**: ARCHITECTURE SPEC — not yet implemented
**Author**: Claude (assisted)  ·  **Date**: 2026-04-09  ·  **For**: Antonio Correa

This is the design document for the **Layer 3 Autonomous Research Engine** —
the proactive scheduled-task layer that turns ARIA from a question-answering
service into a system that actively gathers intelligence on its own schedule.

This spec exists so we can scope the build effort, agree on the architecture,
and avoid common foot-guns *before* writing any code. It is the deliverable
for option (b) of the Phase 3 plan; option (a) (the four cherry-pick fixes)
is shipping in the same commit as this document.

---

## 1. The problem this solves

ARIA today is **reactive**: a user asks a question, ARIA answers. The
five persistent brain layers (RAG, intel ledger, neural memory, knowledge
facts, mem0) all grow only when a user drives a chat turn. There is no
mechanism for ARIA to:

- Catch a new Mozambique tender at 06:00 Monday before competitors notice
- Detect a UK ECJU export-control rule change the day it ships
- Notice that an active counterparty has been newly designated by OFAC
- Build a Lusophone Africa weekly geopolitical digest without being asked
- Track Turkish OEM activity in CPLP markets continuously

Antonio's **mental-module spec** (2026-04-09) names this layer
explicitly:

> **AUTONOMOUS RESEARCH** = her own investigations (proactive, scheduled
> tasks). She does not wait to be asked. She investigates on her own
> schedule, delivers digests, and updates her memory continuously.

The architecture proposal (`aria_research_architecture.py`) describes a
12-task scheduled engine using the `schedule` Python library + `while
True: schedule.run_pending()` busy loop. **That implementation is the wrong
shape for fly.io.** This document specifies a fly.io-native architecture
that can be built incrementally, fails safely, and integrates with the
infrastructure already running.

---

## 2. Decision: APScheduler in-process, not separate worker

We have three plausible scheduling backends. The trade-offs:

| Backend | Pros | Cons | Recommended? |
|---|---|---|---|
| **fly.io scheduled machines** | Native to fly.io, no extra process, auto-scaled | Coarse cron resolution (1 min minimum), each invocation is a fresh container start (~30s cold-start cost), no shared state with the main API process | ❌ |
| **Separate fly.io worker app** (`aria-autonomous`) | Process isolation, dedicated resource budget, can be scaled independently | Doubles deploy/billing complexity, no shared brain state with the main API (would need IPC over the public HTTP boundary), introduces a second auth token | ❌ |
| **APScheduler inside the existing FastAPI process** | Runs in the same process as the API → direct access to all 5 brain layers, all 14 conditional addenda, the LLM provider, and all observability hooks. Zero deploy complexity. Survives restarts via Redis-backed jobstore. Shares the same cost tracker, trace stream, and verifier. | One extra dependency (`apscheduler`). Slight risk of long-running tasks contending with chat turns for the event loop — mitigated by running each task in `loop.run_in_executor()`. | ✅ **YES** |

**Decision**: APScheduler in-process with a Redis jobstore for crash
recovery. This is the smallest possible change that delivers the
capability without breaking the deploy model.

---

## 3. Module layout

```
aria_service/
├── autonomous/
│   ├── __init__.py
│   ├── engine.py          ← APScheduler bootstrap, lifecycle, pause/resume
│   ├── tasks.py           ← Task class definition + execution wrapper
│   ├── tasks.yaml         ← Declarative task config (3 starter tasks)
│   ├── delivery.py        ← WhatsApp + intel-ledger result routing
│   ├── safety.py          ← Rate limit + cost cap + dedupe + circuit breaker
│   └── AUTONOMOUS_ENGINE.md   ← (this file)
```

### Why YAML for tasks, not Python dicts

The proposal embeds task definitions as Python dicts inline in the
engine class. That makes editing tasks a code change. **YAML lets you
edit the schedule without a deploy** — change Mozambique procurement
from 06:05 to 07:30 by editing one line in `tasks.yaml`, save, no
rebuild. The engine reloads `tasks.yaml` on a SIGHUP-equivalent admin
endpoint (`POST /api/aria/autonomous/reload-tasks`).

---

## 4. Task definition format

```yaml
# tasks.yaml — autonomous research task schedule
# Each task is a structured intelligence-gathering operation.
# Edit this file and POST /api/aria/autonomous/reload-tasks to apply.

tasks:
  - id: DAILY-PROC-ANGOLA
    name: Angola defence procurement scan
    schedule: cron
    cron: "0 6 * * mon-fri"      # 06:00 UTC weekdays
    priority: HIGH
    enabled: true
    timeout_seconds: 180
    cost_cap_usd: 0.15
    tool_chain:
      - tool: deep_research
        entity: "Angola defence tender 2026"
        max_queries: 5
      - tool: deep_research
        entity: "concurso público defesa Angola FAA"
        max_queries: 5
    delivery:
      channels: [whatsapp, mem0, intel_ledger]
      whatsapp_group_id: ${WA_GROUP_INTEL}
      escalate_if:
        - "new tender"
        - "contract award"
        - "FAA"
    mem0_tags: [angola, procurement, tender, daily]

  - id: WEEKLY-COMP-UK
    name: UK ECJU export control update
    schedule: cron
    cron: "0 6 * * tue"          # Tuesday 06:00 UTC
    priority: CRITICAL
    enabled: true
    timeout_seconds: 240
    cost_cap_usd: 0.20
    tool_chain:
      - tool: deep_research
        entity: "UK ECJU SITCL OGEL update 2026"
        max_queries: 5
    delivery:
      channels: [whatsapp, mem0, intel_ledger]
      escalate_if:
        - "SITCL"
        - "OGEL change"
        - "new designation"
        - "Angola sanctions"
        - "Mozambique sanctions"
    mem0_tags: [compliance, ECJU, SITCL, weekly]

  - id: WEEKLY-CP-SCAN
    name: Active counterparty news scan
    schedule: cron
    cron: "0 6 * * wed"          # Wednesday 06:00 UTC
    priority: HIGH
    enabled: true
    timeout_seconds: 300
    cost_cap_usd: 0.30
    tool_chain:
      - tool: counterparty_scan
        # Pulls active counterparty list from mem0 by tag
        mem0_tag: counterparty_active
    delivery:
      channels: [whatsapp, mem0]
      escalate_if:
        - "sanctions"
        - "investigation"
        - "insolvency"
        - "criminal"
    mem0_tags: [counterparty, monitoring, weekly]
```

**Start with 3 tasks, not 12.** The proposal's 12-task schedule is
ambitious but every task is a way for the system to silently fail.
Validate the engine, the delivery chain, the escalation router, and the
cost accounting on these three before adding the next nine.

---

## 5. Safety prerequisites (must exist before first task fires)

These five guardrails are mandatory. Without them, a single buggy task
or cost spike can cascade into runaway behaviour.

### 5.1 Rate limit

```python
# autonomous/safety.py
async def check_rate_limit(task_id: str) -> bool:
    """Token bucket: max 12 task firings per hour across the engine."""
    key = f"crucix:autonomous:rate:{int(time.time() // 3600)}"
    count = await rs.incr(key)
    if count == 1:
        await rs.expire(key, 3600)
    return count <= 12
```

### 5.2 Daily cost cap

```python
async def check_cost_cap() -> tuple[bool, float]:
    """Reject task spawn if today's autonomous cost exceeds $5.00."""
    today = time.strftime("%Y-%m-%d")
    key = f"crucix:autonomous:cost:{today}"
    spent_str = await rs.get(key) or "0"
    spent = float(spent_str)
    return (spent < 5.0, spent)
```

### 5.3 Deduplication

If `DAILY-PROC-ANGOLA` ran 6 hours ago and the entity has not changed,
skip the duplicate run rather than burn tokens. Implementation: hash
the task_id + the resolved entity string + the calendar day, store in
Redis with 24h TTL, skip if hash already present.

### 5.4 Per-task timeout

Every task has `timeout_seconds` in YAML. Wrap the tool chain in
`asyncio.wait_for(...)`. If exceeded, log a warning, mark the task run
as `partial`, deliver whatever was gathered before the timeout. Never
let a task spin forever.

### 5.5 Pause / resume admin endpoints

```
POST /api/aria/autonomous/pause              -- stop all tasks immediately
POST /api/aria/autonomous/resume             -- restart paused engine
POST /api/aria/autonomous/pause-task/{id}    -- disable one task
POST /api/aria/autonomous/run-now/{id}       -- manual fire for testing
GET  /api/aria/autonomous/status             -- engine state + last 20 runs
```

These exist so that **if Phase 3c starts misbehaving in production,
Antonio can stop it from WhatsApp via a slash command without needing
SSH access**. Critical for incident response.

---

## 6. Delivery routing

Every task result flows through `autonomous/delivery.py` which:

1. **Captures the tool output** (already done by the existing tool block)
2. **Runs the constitutional pipeline** — same `aria_engine.aria_chat()`
   path used by interactive chat, so the result is subject to the same
   14 clauses + clause 15 citation discipline, the verifier, the
   honesty judge, and the confidence footer
3. **Formats for delivery** — adds the autonomous-engine header
   (`*ARIA Intelligence Brief — DAILY-PROC-ANGOLA — 2026-04-09 06:00 UTC*`)
4. **Routes by channel**:
   - `mem0` → mem0 store via `summarise_and_store()` so the next
     interactive chat has yesterday's procurement findings on tap
   - `intel_ledger` → `intel_ledger.add_signal()` so the rolling 30-day
     ledger shows the new finding
   - `whatsapp` → POST to seenode `/api/wa-listener/send` (which is now
     auth-gated by the recent fix) — message is rendered through the
     same `_normaliseForWhatsApp()` markdown normaliser used for
     interactive replies
5. **Fires escalation** — if the result text contains any of the
   `escalate_if` keywords, ALSO post a `🚨 ESCALATION` alert to the
   intel channel with the matched keyword highlighted
6. **Persists the trace** — same `trace_stream` infrastructure used for
   chat turns, so `/api/aria/trace/stats` shows autonomous costs
   alongside interactive costs

---

## 7. Failure modes & recovery

| Failure | Detection | Recovery |
|---|---|---|
| Task LLM call times out | `asyncio.wait_for` raises | Mark run as `partial`, deliver what was gathered, warn in trace |
| Task tool returns no data | `tool_used: deep_research` returns `extracted_count: 0` | Skip delivery, log INFO, do NOT push empty alerts to WhatsApp |
| Brave API key revoked | `web_search` returns provider="none" | Engine auto-pauses Brave-dependent tasks until next reload |
| Cost cap exceeded mid-run | `check_cost_cap()` returns False | Reject the task spawn with `circuit_breaker_tripped`, alert via intel ledger |
| WhatsApp delivery fails | `seenode /send` returns non-200 | Retry once with 30s backoff, then store the brief in mem0 only |
| Redis jobstore corruption | APScheduler fails to start | Engine logs FATAL, falls back to in-memory scheduler (jobs survive only until restart) |
| Long task contends with chat | Mean chat latency rises | Run tool in `loop.run_in_executor()`, never inline in the async coroutine |
| Same task fires twice (race) | Dedup hash already present | Skip with `duplicate_run`, no cost incurred |

---

## 8. Phased rollout

### Phase 3c-α — engine bootstrap (target: 1 session)
- `autonomous/engine.py` with APScheduler + Redis jobstore + lifecycle hooks
- `autonomous/tasks.yaml` with **one** task: `DAILY-PROC-ANGOLA`
- `autonomous/safety.py` with rate limit + cost cap + dedupe
- 5 admin endpoints (pause/resume/pause-task/run-now/status)
- Smoke test: `pytest aria_service/tests/test_autonomous.py`
- Deploy targets: fly.io aria-intel only

### Phase 3c-β — delivery integration (target: 1 session)
- `autonomous/delivery.py` with the 3 channels (mem0, intel_ledger, whatsapp)
- Escalation keyword router
- WhatsApp delivery via authenticated `/api/wa-listener/send` (the route
  we just secured in commit `98aa281`)
- Validate: trigger `DAILY-PROC-ANGOLA` manually via `/run-now`, confirm
  the brief lands in WhatsApp + is searchable in mem0 within an hour

### Phase 3c-γ — expand to 3 tasks (target: 1 session)
- Add `WEEKLY-COMP-UK` and `WEEKLY-CP-SCAN` to `tasks.yaml`
- Reload via `/reload-tasks`
- Watch for a full week, tune thresholds based on real signal-to-noise

### Phase 3c-δ — counterparty scan tool (target: 1 session)
- New tool `counterparty_scan` that pulls the active list from mem0
  by tag and runs `deep_research` on each entity
- Wire into the tool chain in `tasks.yaml`

### Phase 3c-ε — expand to remaining 9 tasks (incremental)
- Once Phase 3c-α through δ have run cleanly for ≥7 days, add tasks
  from the architecture proposal one at a time, validating each.
- Final state: 12 scheduled tasks, all integrated with mem0 + intel
  ledger + WhatsApp delivery + escalation router.

**Effort estimate**: Phase 3c-α to γ = ~1 working day total. The remaining
phases are ~30 minutes each as additive YAML edits.

---

## 9. What this is NOT

**Not the Anthropic native `web_search_20250305` tool**. We are
deliberately staying on Brave + DuckDuckGo + the deterministic source-
tier scoring. Reasons covered in the parent proposal review.

**Not a separate Python file dropped into the repo**. The proposal's
`aria_research_architecture.py` is a 1453-line monolith that mixes
prompt text, tool definitions, and the engine class. We extract the
engine into the existing module structure, delete the prompt section
(already in `researcher_principles.py`), and treat the proposal as a
spec, not source code.

**Not a separate worker process**. APScheduler inside the FastAPI
process, with all the existing observability hooks intact.

**Not a 12-task one-shot ship**. Three tasks first, validate, expand.

---

## 10. Open questions

These need Antonio's input before Phase 3c-α starts:

1. **WhatsApp group ID for autonomous briefs** — should the autonomous
   engine post to the same group as interactive ARIA replies, or to a
   dedicated `#aria-intel-feed` group? Recommendation: dedicated, so
   the daily 06:00 brief doesn't pollute the conversational thread.

2. **Daily cost cap** — $5/day starting value. Higher? Lower? Currently
   the trace_stream shows total daily cost ~$1.30 for interactive use.
   Adding 12 autonomous tasks would roughly double that.

3. **Escalation channel** — should `🚨 ESCALATION` alerts go to
   WhatsApp DM (Antonio direct), the group, or both? Depends on the
   sensitivity of the escalation triggers.

4. **Counterparty list source** — do you maintain an active counterparty
   list in mem0 already (tagged `counterparty_active`)? If not, Phase 3c-δ
   needs an upstream step to seed it.

5. **First-task validation window** — how long do you want to run
   `DAILY-PROC-ANGOLA` solo before adding the second task? Recommendation:
   one full week of weekday firings (5 runs).

---

## 11. References

- `aria_research_architecture.py` — the original proposal this spec
  cherry-picks from
- `aria_service/intel/researcher_principles.py` — the methodology
  addendum that the autonomous engine reuses verbatim
- `aria_service/intel/mem0.py` — the storage layer for brief continuity
- `aria_service/intel/intel_ledger.py` — the rolling 30-day signal store
- `lib/whatsapp/waListener.mjs` — the seenode delivery target (now
  auth-gated by `_waRequireAuth` from commit `98aa281`)
- `aria_service/aria_engine.py` — the chat pipeline the engine reuses
  for constitutional discipline on autonomous outputs

---

**Decision needed from Antonio**: should Phase 3c-α start in the next
session, or after a week of validation on the current Phase 3 prep
batch (commits `5054f0b` + `edbd987` + `65a4546`)? My recommendation:
**wait one week**. Watch the verifier grounded rate climb, watch the
honesty score, watch the latency mean. If the Phase 3 prep batch holds,
build Phase 3c-α with confidence. If something regresses, fix it first
and ship the engine on a stable foundation.
