# ARIA Indestructibility Plan — resilience hardening (R-F1412)

**Operator directive 2026-06-07:** make ARIA's whole infrastructure 100% indestructible — able to spin out of blackouts, errors, anything. Stop firefighting, start improving.

**Verdict from a 3-agent resilience audit (grounded, file:line):** wedge **PREVENTION is real** (R-F1341/F1400/F1398/F1342 all intact) — ARIA wedges far less than she used to. But **RECOVERY is theater**: the blackout detector runs on the loop it watches, the deadlock detector watches zero threads, self_healing circuit breakers don't gate hot paths, and "auto-recovery" returns `needs_operator`. The ONLY real recovery was a slow (~2-3.5 min) external Fly kill of a **single machine** — and a *live-but-wedged* process never even triggered it. "Indestructible" = build **real, fast recovery** on top of prevention.

---

## DONE THIS SESSION
- **R-F1417 — self-restart on hard wedge (THE keystone).** The off-loop daemon watchdog (main.py) now, past a hard ceiling (90s, env `ARIA_WEDGE_HARD_CEILING_S`, kill-switch `ARIA_WEDGE_SELF_RESTART_ENABLED`), forces `os._exit(1)` → Fly cold-boots → **ARIA self-recovers from a wedge that was previously permanent-until-operator.** Gated by a tested pure predicate `_should_force_restart` (6/6 tests): fires only when enabled + armed (never during boot) + genuinely wedged (never on a legit slow op). Durable writers are atomic/WAL so exit-mid-write is safe. **Needs deploy to go live.**

---

## HARDENING ROADMAP (priority order; lanes)

### P0 — make recovery REAL (Claude lane unless noted)
1. ✅ **R-F1417 self-restart on hard wedge** — DONE, needs deploy.
2. **Wrap the residual on-loop wedge path** (R-F14xx): `routes/aria.py:9501-9514` fitz PDF *text* extraction runs on the loop (R-F1398 fixed OCR but missed this) — wrap in `asyncio.to_thread` like `document_reader.read_document` already does. Closes the last known wedge cause on the hottest route (WA doc review). **High value, low risk.**
3. **Wrap the unwrapped boot steps** (R-F14xx): `main.py:250-255` (6 `init()` calls) + LLM-chain block have NO try/except and no outer guard → one throw = total outage (the 2026-04-27 F28 class). Wrap each so a subsystem failure degrades instead of dead-the-app. **Single biggest "stay up" fix.** (lifespan smoke §9 mandatory.)

### P0 — durability (no data loss)
4. **fsync the atomic writers** before `os.replace`: `knowledge.py:441`, `intel_ledger.py:191`. Atomic rename survives torn writes but NOT power-loss of unflushed data.
5. **Fix the memory_wal lost-append race** (`memory_wal.py:74` append vs `:113-162` read→rewrite) — the "never forget" WAL can drop a fact appended during a drain. Lock or offset-truncate.
6. **Verify the backstop exists (operator/Claude):** `flyctl volumes snapshots list -a aria-intel` (+ aria-web/aria-wa). The only off-machine backstop, currently **UNVERIFIED**. `lib/aria/backup.mjs` reads the WRONG (legacy `runs/`) paths, not `/data` — fix or replace with a real off-machine export of `/data/aria_knowledge.json`+`aria_signals.json`+`aria_state.db`.

### P0 — error resilience (ARIA lane — the CLI; today's trigger)
7. **CLI transient-error classifier misses DNS** (`aria_cli/agent.py:79-82`): `getaddrinfo`/`11001`/`could not reach`/`name resolution` not in `_TRANSIENT_MARKERS` → today's DNS blip failed the whole turn. Classify on httpx exception TYPE (`ConnectError`/`ConnectTimeout`/`ReadTimeout`) not string-sniffing. **Then** give the CLI a fallback chain OR default it to the resilient `aria` server provider (the server survived the same blip; the CLI talking direct to api.deepseek.com did not).

### P1 — fast recovery + faster boot
8. **Readiness vs liveness split** (`main.py`): heavy hydration (87k facts + 55k signals) runs synchronously before `yield` → `/health/live` can't answer + slow ops mask a wedge from Fly. Defer hydration to background (like the semantic index already is); serve "alive, warming." Cuts the cold-boot dark window from ~90s toward ~10-15s AND removes a boot-failure surface.
9. **Tighten Fly health-check for the dead-process case** OR rely on R-F1417 (more reliable — doesn't depend on probe cadence). Current: ~120s to detect a dead process. R-F1417 makes a *wedged* process self-exit in 90s regardless.

### P1 — degrade-not-fail (Claude lane)
10. **Circuit breakers on external APIs that lack them**: `companies_house.py:75` (re-probes a dead/rate-limited API every DD call) + sweep `intel/sources/*.py`. Wire like `web_search.py` already does.
11. **Cap the in-memory state fallback** (`_mem_store`, redis_store.py:42) + alarm louder when `state_backend_reachable=False` (unbounded → OOM on long no-DB uptime).
12. **Proactive disk-space guard** (visibility): `shutil.disk_usage` pre-write check → brain signal at low free (writes are already non-corrupting; this is early warning).

### Cleanup
13. Wire the deadlock detector to real threads OR delete it (watches nothing today = false-confidence theater).

---

## WHAT'S ALREADY SOUND (trust, don't re-touch)
R-F1341 bounded lock · R-F1400 lock-storm waiter-shed · R-F1398 OCR off-loop · R-F1342 non-blocking deploy + bluegreen/canary (autonomous path) · R-F1334 wedge dump · state_store WAL + self-heal reconnect (R-F1397) + critical-write StateWriteError (R-F1351) · server LLM fallback chain + cooldowns + local_brain degrade (§14) · atomic-rename writers (no torn writes) · HF model + Chromium baked into image (no boot download).

## SPOF reality (accept + mitigate, don't fight)
aria-intel/web/wa are each a SINGLE machine on a SINGLE volume; multi-machine HA is structurally blocked by sqlite-on-local-volume. The realistic "indestructible" play for a stateful app is: **fast self-restart (R-F1417) + durable fsync'd writes (#4/#5) + verified snapshots (#6) + minimal cold-boot (#8)** — NOT horizontal HA. Real HA would require moving state to a shared/replicated backend (Phase-B-sized).

## RULES
Every item: own R-number, capability test driving the REAL failure path (a green test while the failure still kills it is WRONG, §23.2), bridge before shared-file edits, lifespan smoke for any main.py boot-path change (§9), careful deploy with health-watch, Claude re-verifies before "done" reaches the operator.
