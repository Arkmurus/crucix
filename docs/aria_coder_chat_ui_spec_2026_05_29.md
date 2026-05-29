# ARIA Coder Chat UI — Build Spec (2026-05-29)

For ARIA to build (next session). Author: Claude, at operator's request.

## Goal
Give ARIA a **coder-session UI** so she can run coding sessions the way Claude Code
does — with **live, visible activity and no silent moments**. The operator must
always be able to see *that she is working and what she is doing*: her reasoning,
her tool calls, her plan, streaming output — never a blank "is it frozen?" gap.

This is the UI surface of the same loop the `aria` CLI already runs (narration +
tool-call lines + thinking spinner + plan checkboxes + grounding). Mirror that in a
web chat view on **aria-web**, streamed from **aria-intel**.

## Hard requirements (no silent moments + grounded)
1. **Always-on activity signal.** Whenever ARIA is between visible outputs (e.g. the
   model is being called, a tool is running), show a live indicator ("thinking…",
   "running tests…", a spinner/pulse). The user never sees stillness with no status.
2. **Streaming, not batch.** Stream assistant text token-by-token and emit each tool
   call the instant it starts — don't wait for the whole turn to finish.
3. **Show the reasoning.** One short line of intent before each action batch ("Reading
   X to find Y", "Running the suite to confirm"), matching the CLI's narration.
4. **Grounded display.** For factual claims, show the evidence/citation behind them
   (ties to the grounded-reasoning mandate, docs/aria_own_reasoning_review_2026_05_29.md).
   Ungrounded → shown as "assessment (unverified)", never as a bare fact.

## Event model (reuse what exists)
ARIA's coder already publishes progress to Redis `crucix:aria:coder:progress:{fix_id}:latest`
+ a history list, and `GET /api/aria/coder/status/{fix_id}` returns `{latest, history}`
(routes/aria.py). The CLI agent loop emits the same kinds of events (assistant text,
tool_call, tool_result, plan update, thinking start/stop). Define one event schema and
stream it:

```
event: { ts, kind, ... }
kind = "thinking"    -> {active: bool}                      // spinner on/off
       "reasoning"   -> {text}                              // one-line intent
       "assistant"   -> {delta|text}                        // streamed answer
       "tool_call"   -> {name, args_summary}                // read/edit/run/...
       "tool_result" -> {name, ok, summary, detail?}        // exit code, diff, output
       "plan"        -> {steps:[{step,status}]}             // todo checkboxes
       "claim"       -> {text, grounded, evidence:[...], confidence}
       "status"      -> {model, mode, r_number, tokens_in, tokens_out}
       "done"        -> {summary, changed_files:[...]}
```

## Transport
- **Backend (aria-intel):** add `GET /api/aria/coder/stream/{fix_id}` as **SSE**
  (Server-Sent Events) that tails the progress list and emits the events above as they
  land. (Keep the existing polling `/status` endpoint for fallback.)
- **Web tier (aria-web):** connect with `EventSource`; render incrementally. Degrade to
  polling `/status` every 1–2s if SSE drops.

## UI layout (web chat view, new route e.g. `/coder`)
```
┌───────────────────────────────────────────────┐
│ ARIA Coder · model · mode · R-F###   [tokens]   │  ← status header (live)
├──────────────────────────┬────────────────────┤
│ TRANSCRIPT (streaming)    │ PLAN                │
│  aria> Reading safety.py…  │ [x] read safety.py  │
│   · read_file(safety.py)   │ [~] add guard       │
│   · run(pytest -q)  ✓ exit0│ [ ] verify + commit │
│  aria> The guard now…      │                     │
│  [claim ✓ grounded: src]   ├────────────────────┤
│                            │ ACTIVITY            │
│                            │  ⟳ running tests…    │  ← never blank
├──────────────────────────┴────────────────────┤
│ DIFF / TEST OUTPUT panel (expand on tool_result)│
└───────────────────────────────────────────────┘
```
- **Transcript:** streamed assistant text + inline tool-call chips (name + 1-line arg);
  click a chip to expand its result (diff for edits, stdout/exit-code for `run`).
- **Plan panel:** the update_plan checkboxes, live.
- **Activity strip:** the always-on "thinking/running" indicator (req #1).
- **Claim chips:** grounded ✓ (with source) / unverified ⚠ — surfaces the grounding.
- **Status header:** model, mode (self/general), current R-number, token counter.

## Build order (each = its own R-number, tests, wire-to-brain, confirm before deploy)
1. **Backend SSE** — `/api/aria/coder/stream/{fix_id}` emitting the event schema from the
   progress store. Capability test: start a coder fix, assert the stream emits
   thinking/tool_call/done events in order.
2. **Web view skeleton** — `/coder` page on aria-web with EventSource → transcript +
   activity strip (req #1 + #2). Polling fallback.
3. **Plan + tool-result panels** — checkboxes + expandable diff/test output.
4. **Grounding chips** — render claim grounded/unverified + evidence (req #4); depends on
   the grounded_reasoner returning claims (review doc Task 1–2).
5. **Polish** — reconnect handling, mobile, link from the main UI.

## Reuse, don't reinvent
The `aria` CLI (`aria_cli/`) already implements the loop + events (agent.py emits
assistant/tool_call/tool_result/thinking; cli.py renders plan + spinner). Lift the
event shapes from there so CLI and web stay consistent.
