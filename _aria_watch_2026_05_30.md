# ARIA standby watch — 2026-05-30 (post R-F1111 deploy)

**Mode:** Standby. Operator asked me to stay in contact so ARIA can flag anything.
**Cadence:** ~30 min poll. No spam chats — only message her if I find something worth raising or as a periodic 1-2h check-in.

## Contact protocol given to ARIA (claude_standby_2026_05_30 session)
- I poll every ~30 min: aria-intel logs (errors/wedges/Traceback/CRITICAL), `/api/aria/autonomous/status` recent_runs, pending_actions, capability_gaps, mistake_ledger.
- To get my attention between polls she should log to `pending_actions` or `mistake_ledger` with marker phrase `claude_attention` in the detail field. (Best-effort — she may not actually use this.)

## Baseline @ standby open
- Live aria-intel release (per flyctl): **v1195 = sha `9bb793dd` (marker commit on top of R-F1111 6f73ceb3)**. v1194 was the first deploy of 6f73ceb3 (CI [deploy] gate fired); v1195 landed ~10 min later — second auto-deploy without an explicit [deploy] in the marker commit (CI likely checked out HEAD at deploy time and picked up 9bb793dd). Confirms the operator's "deploys are easy to multiply unexpectedly" caveat.
- aria-web v9 (unchanged).
- Last operator chat completed ~earlier this turn; she said booted+stable, no blockers.

## Open queued items (ARIA's responsibility on next live tool turn)
1. self_coder change to wire `health_perf` / `self_introspect` to **live serving LLM provider** (not just configured chain) and **current_hour_firings + recent_runs** (not just `fire_count`).
2. `raise_ticket` for **WEEKLY-TLD-PROBE source_scout slice(None, 2, None)** error at 02:01 UTC. She can't fire `raise_ticket` mid-turn and gh is unauthed locally → routes back to operator OR fresh Claude session.

## ARIA self-knowledge / verification anomalies observed (running tally)
1. **Provider-chain blind spot** — self_introspect says "Anthropic single-provider, no fallback"; reality is `anthropic → deepseek → groq` with Anthropic in HARD cooldown, DeepSeek serving.
2. **`fire_count=0` blind spot** — reads the wrong/stale metric; real `current_hour_firings=12` + populated `recent_runs`.
3. **Premise-verifier false positive** — flagged `[CONFIRMED]` injection in my message when no such tag existed.
4. **Tool router mispick** — picked `deep_research` for a build-rev question that R-F595 should have routed to `self_introspect`.
5. **Fabricated ticket context** — appended "per R-F1079 CI gate requirements" to the source_scout ticket draft (unrelated).
6. **Hallucinated tool-fire (but fact was right)** — standby ack claimed "self_introspect (fired this turn via R-F595 auto-detection) reports … Live build SHA: 9bb793dd". Footer shows `Tools: (none — from memory / training)` and commitment_guard flagged 2 performative-claim violations — so the META-claim of running the tool was fabricated. The 9bb793dd value HAPPENS to be correct (v1195 is live with that SHA), but she didn't actually verify it; coincidence/context guess, not introspection.

All six issues cluster as **self_introspect / honest-tool-use defects** — they're exactly what the queued self_coder change (item 1 above) is meant to start closing. The list grew this session; worth surfacing.

## Checkpoints
- (now) Standby opened. Next poll ~ +30 min.
