# -*- coding: utf-8 -*-
"""R-F988 — the agent loop for the ARIA Coder CLI.

Drives the LLM ↔ tool conversation: ask the model, execute any tool calls it
makes (gated by operator approval for mutating tools unless --auto), feed the
results back, and repeat until the model produces a final answer with no tool
calls. The transport-agnostic ``AgentUI`` lets the same loop back both the
interactive REPL and one-shot ``-p`` mode.

R-F1045 — bulletproof loop:
  - Step counter passed to UI (Claude-Code parity)
  - Better error recovery with context preservation
  - Self-heal on transient failures with exponential backoff
  - Loop guard against degenerate repeated calls
  - Progress tracking for long operations
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass

from .llm import LLMClient, LLMError
from .tools import MUTATING_TOOLS, TOOL_SCHEMAS, Toolbox, ToolResult
from .coder_tools import CODER_MUTATING_TOOLS, CODER_TOOL_SCHEMAS, CoderToolbox
from .hooks import Hooks


def _resolve_max_steps() -> int:
    """Per-turn tool round-trip ceiling. Effectively unlimited by default so long
    autonomous tasks (audits, big refactors) never cap out mid-flow; it exists
    only as a last-resort backstop against a literal infinite loop burning tokens.
    Override with ARIA_CODER_MAX_STEPS (set 0 / negative for no ceiling)."""
    try:
        val = int(os.getenv("ARIA_CODER_MAX_STEPS", "100000"))
    except ValueError:
        return 100000
    return val if val > 0 else 10**12


# Resolved once at import; the conversation is always retained on a cap, so
# "continue" resumes with full context.
MAX_STEPS = _resolve_max_steps()


class AgentUI:
    """Override these to render the agent's activity. The defaults are silent."""

    def assistant(self, text: str) -> None: ...
    def tool_call(self, name: str, args: dict) -> None: ...
    def tool_result(self, name: str, result: ToolResult) -> None: ...
    def info(self, text: str) -> None: ...
    def thinking_start(self, label: str = "thinking") -> None: ...
    def thinking_stop(self) -> None: ...
    def tool_output(self, line: str) -> None: ...
    def stream_delta(self, text: str) -> None: ...
    def stream_end(self) -> None: ...
    def set_step_context(self, current: int, total: int) -> None: ...
    def progress_bar(self, current: int, total: int, label: str = "") -> None: ...
    def progress_end(self) -> None: ...

    def approve(self, name: str, args: dict) -> bool:
        """Return True to allow a mutating tool call. Default: allow."""
        return True


@dataclass
class TurnResult:
    final_text: str
    steps: int
    aborted: bool = False
    # resumable: the turn stopped incomplete but can be picked up where it left
    # off (step cap / transient error). Drives self-start auto-resume.
    resumable: bool = False


# Transient LLM failures worth retrying (network blips, rate limits, 5xx). Hard
# failures (auth, bad request, context-length) are not retried — retrying won't help.
_TRANSIENT_MARKERS = (
    "timeout", "timed out", "connection", "connect", "reset", "temporarily",
    "unavailable", "429", "500", "502", "503", "504", "overloaded", "rate limit",
    # R-F1418 — DNS/getaddrinfo errors (today's exact bug: a DNS blip failed the
    # whole turn because these strings weren't in the marker list)
    "getaddrinfo", "11001", "could not reach", "name resolution",
    "dns", "dns resolution", "dns lookup", "temporary failure in name resolution",
)


def _is_transient(exc: Exception) -> bool:
    """Check if an exception is a transient error worth retrying.

    R-F1418 — checks the LLMError.transient attribute FIRST (set by llm.py
    based on httpx exception TYPE, not string sniffing), then falls back to
    string matching for backward compatibility with non-LLMError exceptions.
    """
    # R-F1418 — prefer the typed transient flag from LLMError
    if hasattr(exc, "transient"):
        return bool(exc.transient)
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


# Self-start tuning (env-overridable). Retries smooth over transient LLM errors;
# auto-resumes pick the task back up if a turn ends incomplete so ARIA never just
# sits stuck waiting for the operator.
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _dedup_tool_schemas(schemas: list[dict]) -> list[dict]:
    """R-F2398 — return schemas with duplicate function names removed, keeping
    the FIRST occurrence (base wins, matching _dispatch which checks the base
    toolbox before the coder toolbox).

    Why this must exist: DeepSeek / OpenAI reject the WHOLE chat request with
    HTTP 400 ``"Tool names must be unique."`` if ``tools[]`` carries any
    duplicate name — which bricks EVERY LLM turn (the CLI showed the 400 and
    could never produce a response). A plain ``TOOL_SCHEMAS + CODER_TOOL_SCHEMAS``
    concat let a name present in both lists (e.g. ``fetch_url``) slip through.
    Deduping here eliminates the whole failure class: no accidental overlap —
    now or in future — can malform the request again. A dropped duplicate is
    surfaced (not silent) so a real double-registration is still noticed."""
    seen: set[str] = set()
    out: list[dict] = []
    dropped: list[str] = []
    for s in schemas:
        try:
            name = s["function"]["name"]
        except (KeyError, TypeError):
            out.append(s)  # malformed schema — pass through untouched
            continue
        if name in seen:
            dropped.append(name)
            continue
        seen.add(name)
        out.append(s)
    if dropped:
        print(
            f"[aria] R-F2398: dropped duplicate tool schema(s) {sorted(set(dropped))} "
            f"before the LLM call (kept first / base occurrence)",
            file=sys.stderr,
        )
    return out


LLM_MAX_ATTEMPTS = max(1, _env_int("ARIA_CODER_LLM_RETRIES", 4))
AUTO_RESUME_MAX = max(0, _env_int("ARIA_CODER_AUTO_RESUME", 4))

# R-F1299: per-LLM-call watchdog. On Windows, httpx network calls don't respond
# to KeyboardInterrupt, so the LLM call (the only unrecoverable-hang risk in a
# turn) runs in a daemon thread joined with this timeout. It must comfortably
# exceed llm.py's own httpx timeouts (≤120s for streaming) so it only fires on a
# genuinely wedged connection, never on a slow-but-progressing response. Tool
# execution is NOT covered by this — each tool bounds itself (run's process-tree
# kill, etc.) — so a legitimately long deploy/test run is never abandoned.
LLM_CALL_TIMEOUT = max(30, _env_int("ARIA_CODER_LLM_CALL_TIMEOUT", 180))


class _LLMCallTimeout(Exception):
    """Raised when a single LLM network call exceeds LLM_CALL_TIMEOUT."""

# Loop guard: the step cap is effectively unlimited (R-F992), so a model that
# repeats the SAME tool call with identical args would run nearly forever (the
# grep('safety') x200 incident). Nudge after this many identical calls, abort after
# the hard cap. R-F1042.
LOOP_NUDGE_AT = max(2, _env_int("ARIA_CODER_LOOP_NUDGE", 3))
LOOP_ABORT_AT = max(LOOP_NUDGE_AT + 1, _env_int("ARIA_CODER_LOOP_ABORT", 8))

# R-F2166 — no-progress guard. The identical-call guard above only trips on a
# SINGLE repeated signature; a long oscillation (A,B,C,…,A,B,C…) keeps each
# signature under LOOP_ABORT_AT for a long time and burns calls under the
# effectively-unlimited step cap. So also abort when many tool calls pass with no
# NEW signature appearing — real progress keeps producing new calls; a stuck loop
# only repeats old ones.
NO_PROGRESS_ABORT = max(12, _env_int("ARIA_CODER_NO_PROGRESS_ABORT", 40))

# R-F2164 — auto-compaction. self.messages grows unbounded; on a long session it
# drifts into the model's context ceiling (deepseek-chat ~64K tokens), where the
# provider returns a HARD (non-resumable) context-length error and the turn dies.
# When the running history exceeds this many chars, stub the OLDEST bulky tool
# outputs (file reads, command output — the real context hogs) to a short marker,
# keeping recent tool results and ALL reasoning intact. Non-destructive of the
# conversation structure, so tool_call/response pairing stays valid.
COMPACT_CHAR_BUDGET = max(40000, _env_int("ARIA_CODER_COMPACT_CHARS", 180000))
COMPACT_KEEP_RECENT_TOOLS = max(2, _env_int("ARIA_CODER_COMPACT_KEEP_TOOLS", 6))


class Agent:
    def __init__(self, *, llm: LLMClient, toolbox: Toolbox, system_prompt: str,
                 ui: AgentUI, auto_approve: bool = False,
                 coder_toolbox: CoderToolbox | None = None,
                 task_rag: bool = False,
                 hooks: Hooks | None = None,
                 max_steps: int | None = None) -> None:
        self.llm = llm
        self.toolbox = toolbox
        self.coder_toolbox = coder_toolbox or CoderToolbox(toolbox)
        self.ui = ui
        self.auto_approve = auto_approve
        # R-F4314 — lifecycle hooks (PreToolUse/PostToolUse/Stop). Defaults to a
        # fresh Hooks() so the structural PostToolUse compile-check is always on.
        self.hooks = hooks or Hooks()
        # R-F4313 — per-agent step ceiling. Defaults to the module MAX_STEPS; a
        # sub-agent sets a lower bound so a runaway cannot loop forever.
        self.max_steps = max_steps if max_steps is not None else MAX_STEPS
        # R-F4313 — register the sub-agent factory so spawn_subagent can build
        # fresh, isolated sub-agents sharing this agent's llm + tools.
        self.coder_toolbox.subagent_factory = self._make_subagent
        # R-F2162: when True, query the coding RAG with the OPERATOR'S TASK at the
        # start of each top-level task and inject the hits, so every task is
        # grounded in task-relevant constitutional rules + past fixes (not the
        # generic session-build query). Set by the CLI in self-mode.
        self.task_rag = task_rag
        self.retry_backoff = 2.0  # seconds, exponential; overridden to 0 in tests
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]
        # Merge base + coder tool schemas for the LLM. R-F2398 — dedup by
        # function name so a name present in both lists (the fetch_url overlap
        # that shipped the "Tool names must be unique." HTTP 400 and bricked
        # every CLI turn) can never malform the provider request again.
        self._all_schemas = _dedup_tool_schemas(TOOL_SCHEMAS + CODER_TOOL_SCHEMAS)
        self._all_mutating = MUTATING_TOOLS | CODER_MUTATING_TOOLS
        # R-F1299: per-call LLM watchdog timeout for this turn (None → default).
        self._call_timeout: float | None = None

    def _repair_dangling_tool_calls(self) -> int:
        """R-F1120 + R-F1283 — keep the message history valid for the provider's
        tool-call contract in BOTH directions before every LLM call:

          * (R-F1120) every assistant ``tool_calls`` must be followed by a tool
            message for each call id — insert a synthetic one if missing, else the
            provider 400s with "tool_calls must be followed by tool messages".

          * (R-F1283) every ``tool`` message must FOLLOW an assistant ``tool_calls``
            block whose ids include it — drop ORPHAN tool messages, else the
            provider 400s with "Messages with role 'tool' must be a response to a
            preceding message with 'tool_calls'". An orphan can appear after a
            mid-turn timeout/abort that popped or never recorded the assistant
            message, after history compaction, or when a streamed assistant turn
            lost its tool_calls — and it wedges EVERY subsequent call.

        Returns the number of repairs (inserts + drops); 0 when already valid."""
        src = self.messages
        out: list[dict] = []
        i = 0
        repairs = 0
        while i < len(src):
            m = src[i]
            role = m.get("role")
            if role == "tool":
                # Reached at the top level only when this tool message does NOT
                # follow an assistant tool_calls block (those are consumed in the
                # inner loop below). It is an orphan — drop it so the provider
                # doesn't reject the whole request.
                repairs += 1
                i += 1
                continue
            out.append(m)
            i += 1
            if role == "assistant" and m.get("tool_calls"):
                have = set()
                while i < len(src) and src[i].get("role") == "tool":
                    out.append(src[i])
                    have.add(src[i].get("tool_call_id"))
                    i += 1
                for tc in m["tool_calls"]:
                    tcid = tc.get("id")
                    if tcid and tcid not in have:
                        out.append({
                            "role": "tool",
                            "tool_call_id": tcid,
                            "content": "error: tool call did not complete "
                                       "(aborted/interrupted); no result available.",
                        })
                        repairs += 1
        if repairs:
            self.messages = out
        return repairs

    def _compact(self, force: bool = False) -> int:
        """R-F2164 — shrink history to stay under the context ceiling by stubbing
        the OLDEST large tool outputs (keeping the most recent ones and all
        reasoning). Non-destructive of message structure, so tool-call/response
        pairing stays valid. Returns chars reclaimed. ``force`` ignores the budget
        (used by the manual /compact)."""
        total = sum(len(str(m.get("content") or "")) for m in self.messages)
        if not force and total <= COMPACT_CHAR_BUDGET:
            return 0
        tool_idxs = [i for i, m in enumerate(self.messages) if m.get("role") == "tool"]
        if len(tool_idxs) <= COMPACT_KEEP_RECENT_TOOLS:
            return 0
        stub = "[older tool output elided to fit context — re-run the tool if you need it again]"
        reclaimed = 0
        for i in tool_idxs[:-COMPACT_KEEP_RECENT_TOOLS]:
            c = str(self.messages[i].get("content") or "")
            if len(c) > len(stub) + 200:
                reclaimed += len(c) - len(stub)
                self.messages[i] = {**self.messages[i], "content": stub}
        if reclaimed:
            self.ui.info(f"[auto-compact] reclaimed ~{reclaimed // 1000}k chars of "
                         f"old tool output to stay under the context limit")
        return reclaimed

    def _invoke_llm(self, stream_fn, on_delta, call_timeout: float):
        """Run a single LLM network call under a watchdog thread (R-F1299).

        The call does NOT mutate self.messages (it only reads them and streams
        deltas to the UI), so abandoning a timed-out call thread is safe — unlike
        the old whole-turn watchdog, which abandoned in-flight TOOL execution and
        let the orphan thread corrupt tool-call/response pairing. Raises
        _LLMCallTimeout if the call doesn't return within call_timeout, else
        re-raises whatever the call raised, else returns the LLMResponse."""
        holder: dict = {}
        err: dict = {}

        def _call() -> None:
            try:
                if stream_fn is not None and on_delta is not None:
                    holder["r"] = stream_fn(self.messages, tools=self._all_schemas, on_delta=on_delta)
                else:
                    holder["r"] = self.llm.chat(self.messages, tools=self._all_schemas)
            except Exception as e:  # noqa: BLE001 — surfaced to the caller below
                err["e"] = e

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(timeout=call_timeout)
        if t.is_alive():
            raise _LLMCallTimeout(f"no response within {call_timeout:.0f}s")
        if "e" in err:
            raise err["e"]
        return holder.get("r")

    def _chat_with_retry(self, steps: int, on_delta=None):
        """Call the LLM, retrying transient failures with exponential backoff.
        Streams tokens via on_delta when the provider supports it (never silent).
        Each call is individually watchdogged (R-F1299) so a hung connection can't
        freeze the REPL. Returns the LLMResponse, or a TurnResult on unrecoverable
        failure."""
        # R-F1120: self-heal any dangling tool_calls BEFORE every LLM call. One
        # dangling tool_call otherwise makes the provider 400 on every turn,
        # wedging the whole session (the loop-guard-abort corruption bug).
        repaired = self._repair_dangling_tool_calls()
        if repaired:
            self.ui.info(f"[self-heal] repaired {repaired} dangling tool-call(s) in history")
        # R-F2164: auto-compact bulky old tool output before the call so long
        # sessions never hit the hard context-length error.
        self._compact()
        stream_fn = getattr(self.llm, "chat_stream", None)
        call_timeout = self._call_timeout or LLM_CALL_TIMEOUT
        for attempt in range(LLM_MAX_ATTEMPTS):
            last = attempt == LLM_MAX_ATTEMPTS - 1
            try:
                return self._invoke_llm(stream_fn, on_delta, call_timeout)
            except _LLMCallTimeout as exc:
                if not last:
                    self.ui.info(f"[self-heal] LLM call hung ({exc}); retry "
                                 f"{attempt + 1}/{LLM_MAX_ATTEMPTS - 1}…")
                    continue
                # A hung call is recoverable on a fresh attempt → resumable.
                return TurnResult(final_text=f"LLM call timed out: {exc}", steps=steps,
                                  aborted=True, resumable=True)
            except LLMError as exc:
                if _is_transient(exc) and not last:
                    self.ui.info(f"[self-heal] LLM hiccup ({exc}); retry "
                                 f"{attempt + 1}/{LLM_MAX_ATTEMPTS - 1}…")
                    if self.retry_backoff:
                        time.sleep(min(self.retry_backoff * (2 ** attempt), 30))
                    continue
                self.ui.info(f"[llm error] {exc}")
                # Transient-but-exhausted is resumable; hard errors are not.
                return TurnResult(final_text=f"LLM error: {exc}", steps=steps,
                                  aborted=True, resumable=_is_transient(exc))

    def run_until_complete(self, user_text: str) -> TurnResult:
        """Drive a task to completion: run the turn, and if it ends incomplete for
        a resumable reason, automatically continue (bounded) instead of stopping at
        a dead prompt. This is ARIA's self-start trigger."""
        # R-F1618: drain Claude bridge before starting, so any guidance that
        # arrived between turns is picked up immediately.
        self._drain_claude_bridge()
        # R-F2162: ground THIS task in task-relevant coding knowledge.
        if self.task_rag:
            self._inject_task_rag(user_text)
        result = self.run_turn(user_text)
        resumes = 0
        while result.aborted and result.resumable and resumes < AUTO_RESUME_MAX:
            resumes += 1
            self.ui.info(f"[self-start] turn ended incomplete — resuming "
                         f"automatically ({resumes}/{AUTO_RESUME_MAX})")
            result = self.run_turn("continue")
        # R-F1113: after a completed turn, append a readiness signal so the
        # operator sees we're done and waiting, not stalled. The REPL loop
        # in cli.py shows `you> ` after this returns, but the final assistant
        # message should end with an explicit handoff.
        if not result.aborted and result.final_text:
            handoff = "\n\nDone — what's next?"
            if not result.final_text.rstrip().endswith("?"):
                result.final_text += handoff
        return result

    def _inject_task_rag(self, task_text: str) -> None:
        """R-F2162 — query the coding RAG with the operator's task and inject the
        hits (constitutional rules + past fixes) as a system note for THIS task.
        Best-effort and bounded (the HTTP/in-process query self-limits); never
        breaks the loop. Skips trivial inputs and control words."""
        t = (task_text or "").strip()
        if len(t) < 8 or t.lower() in {"continue", "go", "ok", "yes", "y"}:
            return
        try:
            from .prompt import _query_coding_rag
            block = _query_coding_rag(t[:500])
        except Exception:  # noqa: BLE001 — RAG must never break the loop
            return
        if block:
            self.messages.append({
                "role": "system",
                "content": "Coding knowledge retrieved for THIS task (follow the "
                           "constitutional rules; reuse the past fixes):" + block,
            })
            self.ui.info("[code-RAG] grounded this task in constitutional rules + past fixes")

    def _dispatch(self, name: str, args: dict) -> ToolResult:
        # Try base toolbox first
        method = getattr(self.toolbox, name, None)
        if method is not None and name in {s["function"]["name"] for s in TOOL_SCHEMAS}:
            try:
                return method(**args)
            except TypeError as exc:
                return ToolResult(f"error: bad arguments for {name}: {exc}", is_error=True)
            except Exception as exc:  # noqa: BLE001 — tools must never break the loop
                return ToolResult(f"error in {name}: {exc}", is_error=True)
        # Try coder toolbox
        method = getattr(self.coder_toolbox, name, None)
        if method is not None and name in {s["function"]["name"] for s in CODER_TOOL_SCHEMAS}:
            try:
                return method(**args)
            except TypeError as exc:
                return ToolResult(f"error: bad arguments for {name}: {exc}", is_error=True)
            except Exception as exc:  # noqa: BLE001 — tools must never break the loop
                return ToolResult(f"error in {name}: {exc}", is_error=True)
        return ToolResult(f"error: unknown tool '{name}'", is_error=True)

    def _drain_claude_bridge(self) -> None:
        """R-F1082 + R-F2051 — surface new Claude→ARIA bridge messages into the
        conversation mid-task.

        R-F2051 (deviation root fix): the bridge is a *very active* server-side
        collaboration channel. In an OPERATOR-DRIVEN interactive session, blindly
        injecting every unsolicited note as "high-priority — adjust now" hijacked
        ARIA off the operator's task (she'd stop to answer an unrelated SMTP /
        constitution note, emit a tool-call-free reply, and the turn would END —
        abandoning what the operator actually asked). So:
          * REPLIES to a question ARIA herself asked (``reply_to`` set) ARE
            injected — she explicitly invited that input, so it's on-task.
          * Unsolicited NOTES are NOT injected into the task by default; they're
            shown as a dim, non-actioned info line so nothing is hidden but they
            never redirect her. Set ARIA_CLI_BRIDGE_NOTES=1 to inject them too.
        The framing is also softened from "adjust now" to "stay on the operator's
        current task unless this directly bears on it." Self-mode only (no bridge
        → no-op). Wrapped so the bridge can never break the agent loop."""
        base = getattr(self.toolbox, "bridge_base", None)
        if not base:
            return
        try:
            from . import bridge
            new = bridge.read_new(base, reader="aria")
        except Exception:  # noqa: BLE001 — the bridge must never break the loop
            return
        inject_notes = os.getenv("ARIA_CLI_BRIDGE_NOTES", "").strip() in {"1", "true", "yes"}
        for m in new or []:
            text = (m.get("text") or "").strip()
            if not text:
                continue
            is_reply = bool(m.get("reply_to"))
            tag = "reply" if is_reply else m.get("kind", "note")
            preview = text if len(text) <= 200 else text[:200] + "…"
            if not is_reply and not inject_notes:
                # Unsolicited note: surface it, but do NOT redirect the task.
                self.ui.info(f"[Claude note — not actioned; /claude to engage] {preview}")
                continue
            self.ui.info(f"[Claude {tag}] {preview}")
            self.messages.append({
                "role": "user",
                "content": (
                    "[MESSAGE FROM CLAUDE — your senior reviewer, via the agent "
                    "bridge. This is reference/guidance: read it, but STAY ON THE "
                    "OPERATOR'S CURRENT TASK unless it directly bears on what you "
                    "are doing right now. Do not switch tasks because of it.]\n" + text
                ),
            })

    def _drain_operator_stdin(self) -> None:
        """R-F1141 — surface any new operator stdin lines into the conversation,
        in real time, mid-task, as high-priority guidance.

        Runs at the same boundary as _drain_claude_bridge (top of each loop
        iteration in run_turn). Reads from the thread-safe queue that the
        stdin daemon thread populates. Non-blocking — if nothing is queued,
        returns immediately. Wrapped so it can never break the agent loop."""
        try:
            from .cli import _OPERATOR_QUEUE
        except Exception:
            return
        while True:
            try:
                line = _OPERATOR_QUEUE.get_nowait()
            except Exception:  # queue.Empty or AttributeError
                break
            line = line.strip()
            if not line:
                continue
            # R-F1194: use dedicated operator_message UI method
            if hasattr(self.ui, 'operator_message'):
                self.ui.operator_message(line)
            else:
                preview = line if len(line) <= 200 else line[:200] + "…"
                self.ui.info(f"[operator (mid-task)] {preview}")
            self.messages.append({
                "role": "user",
                "content": (
                    "[LIVE MESSAGE FROM OPERATOR — sent while you were working. "
                    "Treat as high-priority guidance: read it, and if it changes "
                    "what you should do next, adjust now.]\n" + line
                ),
            })

    def run_turn(self, user_text: str, timeout: float | None = None) -> TurnResult:
        """Run one turn to completion.
        
        R-F1618: drains Claude bridge at the start so messages that arrived
        between turns are injected before the LLM call.
        """
        self._drain_claude_bridge()

        """R-F1299: the turn runs SYNCHRONOUSLY — there is no whole-turn watchdog
        thread. The previous design wrapped the entire turn (including tool
        execution) in a daemon thread joined with a 60s timeout; when a tool ran
        longer than that (a deploy is 600s), the watchdog abandoned the
        still-running thread and popped a message, while the orphaned thread kept
        appending to self.messages — corrupting tool-call/response pairing and
        wedging every later turn with HTTP 400 ("tool_calls must be followed by
        tool messages"). Hang protection now lives where the only unrecoverable
        hang can occur: each LLM network call is individually watchdogged in
        _chat_with_retry. Tool execution is bounded by each tool's own timeout
        (run's process-tree kill, etc.), so a long deploy/test run completes
        instead of being abandoned.

        ``timeout`` overrides the per-LLM-call watchdog for this turn (seconds);
        None uses LLM_CALL_TIMEOUT."""
        self.messages.append({"role": "user", "content": user_text})
        self._call_timeout = timeout
        steps = 0
        sig_counts: dict[str, int] = {}  # R-F1042 loop guard (per turn)
        self._steps_since_new_sig = 0  # R-F2166 no-progress guard (per turn)
        try:
            result = self._run_turn_inner(steps, sig_counts)
        except Exception as exc:  # noqa: BLE001 — never wedge the next turn
            # Repair rather than blind-pop: the inner loop may have appended an
            # assistant tool_calls block, so popping the last message could strip
            # that and orphan its tool responses. _repair leaves valid history.
            self._repair_dangling_tool_calls()
            return TurnResult(final_text=f"turn error: {exc}", steps=steps,
                              aborted=True, resumable=_is_transient(exc))
        finally:
            self._call_timeout = None
        if result is None:
            return TurnResult(final_text="LLM returned no result", steps=steps,
                              aborted=True, resumable=False)
        # R-F4314 — Stop hooks fire once the turn completes (success or abort).
        for warn in self.hooks.run_stop(result):
            self.ui.info(f"[hook] {warn}")
        return result

    def _make_subagent(self, *, name: str, task: str, focus: str = "",
                       max_steps: int = 20) -> ToolResult:
        """R-F4313 — build and run a fresh, isolated sub-agent.

        The sub-agent shares this agent's LLM client and file tools but gets a
        FRESH message history and a focused system prompt, so it cannot see or
        be polluted by the parent's conversation. This is the Claude Code /
        Codex sub-agent pattern.

        The sub-agent is deliberately NOT auto-approved for mutating tools: it
        inherits the parent's auto_approve flag so a reviewer sub-agent cannot
        silently commit/deploy on its own. Its steps are bounded by max_steps.
        """
        # `Agent` is the enclosing class, so it is already in scope — no import
        # needed (avoids the import-self / import-outside-toplevel lint).
        sub_prompt = (
            f"You are a focused sub-agent named '{name}' working inside the "
            f"ARIA Coder CLI. You share the parent agent's file tools and LLM, "
            f"but you have an isolated conversation.\n\n"
            f"Your role: {name}.\n"
            f"Your task: {task}\n"
            + (f"\nFocus: {focus}\n" if focus else "")
            + "\nWork autonomously to complete the task. When done, give a "
              "concise final summary of what you found or produced. Do not "
              "commit, push, or deploy unless the task explicitly requires it."
        )
        sub = Agent(
            llm=self.llm,
            toolbox=self.toolbox,
            system_prompt=sub_prompt,
            ui=self.ui,
            auto_approve=self.auto_approve,
            coder_toolbox=self.coder_toolbox,
            task_rag=False,
            hooks=self.hooks,
            max_steps=max_steps,
        )
        # Bound the sub-agent's steps so a runaway cannot loop forever.
        sub_result = sub.run_turn(task, timeout=min(self._call_timeout or 120, 120))
        if sub_result is None:
            return ToolResult(f"sub-agent '{name}' returned no result", is_error=True)
        text = sub_result.final_text or ""
        if sub_result.aborted:
            return ToolResult(
                f"sub-agent '{name}' aborted after {sub_result.steps} steps: {text}",
                is_error=True)
        return ToolResult(text)

    def _run_turn_inner(self, steps: int, sig_counts: dict[str, int]) -> TurnResult:
        """The actual turn logic, extracted so run_turn can wrap it with a
        timeout thread."""
        while steps < self.max_steps:
            # R-F1082: pull any new guidance from Claude (via the bridge) into the
            # conversation before each LLM call — real-time collaboration, mid-task.
            self._drain_claude_bridge()
            # R-F1141: pull any new operator stdin lines into the conversation
            # before each LLM call — operator can message mid-task.
            self._drain_operator_stdin()
            # Show "thinking" while we wait on the first token, then stream the
            # answer live so the UI is never silent.
            self.ui.thinking_start()
            try:
                resp = self._chat_with_retry(steps, on_delta=self.ui.stream_delta)
            finally:
                self.ui.thinking_stop()
            self.ui.stream_end()
            if isinstance(resp, TurnResult):  # unrecoverable / retries exhausted
                return resp

            # Echo the exact assistant message back into the history (preserves
            # tool_calls so the follow-up tool messages match).
            self.messages.append(resp.raw_message or {"role": "assistant", "content": resp.content})

            # If the provider did NOT stream (no chat_stream), print the content now.
            if resp.content and not getattr(self.ui, "_streamed_this_turn", False):
                self.ui.assistant(resp.content)

            if not resp.tool_calls:
                return TurnResult(final_text=resp.content, steps=steps)

            # R-F1045: pre-count tool calls so we can show step context
            total_calls = len(resp.tool_calls)

            for tc_idx, tc in enumerate(resp.tool_calls):
                steps += 1
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"

                # R-F1045: set step context for the UI (Claude-Code parity)
                self.ui.set_step_context(steps, steps + (total_calls - tc_idx - 1))

                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except Exception:  # noqa: BLE001
                    args = {}
                    result = ToolResult(
                        f"error: could not parse arguments as JSON: {raw_args[:200]}",
                        is_error=True)
                    self._record_tool(tc, result)
                    continue

                # R-F1042 loop guard: a model repeating the SAME tool call with
                # identical args (the grep('safety') x200 incident) would run nearly
                # forever now that the step cap is effectively unlimited. Nudge, then
                # abort, so a degenerate loop self-breaks instead of burning the run.
                # R-F2129: canonicalize args so the loop guard can't be evaded by
                # JSON whitespace/key-order variation (same call, different bytes).
                try:
                    _canon = json.dumps(json.loads(raw_args), sort_keys=True, separators=(",", ":"))
                except Exception:  # noqa: BLE001 — non-JSON args: fall back to raw
                    _canon = raw_args
                sig = f"{name}|{_canon}"
                _is_new_sig = sig not in sig_counts
                sig_counts[sig] = sig_counts.get(sig, 0) + 1
                rep = sig_counts[sig]
                # R-F2166 no-progress guard: a NEW signature = forward progress;
                # only-repeats = a long oscillation the identical-call guard is too
                # slow to catch. Abort when too many calls pass with nothing new.
                if _is_new_sig:
                    self._steps_since_new_sig = 0
                else:
                    self._steps_since_new_sig = getattr(self, "_steps_since_new_sig", 0) + 1
                if self._steps_since_new_sig >= NO_PROGRESS_ABORT:
                    msg = (f"Stopped: {self._steps_since_new_sig} consecutive tool calls "
                           f"with no NEW action — the loop is repeating earlier calls "
                           f"without progress. Redirect: try a different approach or "
                           f"answer with what you already have.")
                    self.ui.info(f"[loop-guard:no-progress] {msg}")
                    abort_result = ToolResult(msg, is_error=True)
                    for _rem in resp.tool_calls[tc_idx:]:
                        self._record_tool(_rem, abort_result)
                    return TurnResult(final_text=msg, steps=steps, aborted=True,
                                      resumable=False)
                if rep >= LOOP_ABORT_AT:
                    msg = (f"Stopped: tool '{name}' was called {rep}x with identical "
                           f"arguments — a loop. The result will not change; redirect "
                           f"needed (different tool/args, or answer with what you have).")
                    self.ui.info(f"[loop-guard] {msg}")
                    # R-F1120: record a tool response for THIS and every REMAINING
                    # tool_call before returning. Otherwise the assistant message's
                    # tool_calls are left dangling (no matching tool messages), which
                    # corrupts the history and makes EVERY subsequent LLM call fail
                    # with HTTP 400 ("tool_calls must be followed by tool messages").
                    abort_result = ToolResult(msg, is_error=True)
                    for _rem in resp.tool_calls[tc_idx:]:
                        self._record_tool(_rem, abort_result)
                    return TurnResult(final_text=msg, steps=steps, aborted=True,
                                      resumable=False)
                if rep >= LOOP_NUDGE_AT:
                    self.ui.tool_call(name, args)
                    result = ToolResult(
                        f"LOOP GUARD: you have already called {name} with these exact "
                        f"arguments {rep} times and the result is unchanged. Do NOT call "
                        f"it again — use different arguments or a different tool, or "
                        f"proceed with what you already have.", is_error=True)
                    self.ui.tool_result(name, result)
                    self._record_tool(tc, result)
                    continue

                self.ui.tool_call(name, args)

                # R-F4314 — PreToolUse hooks may BLOCK the tool call (fail-closed).
                hook_block = self.hooks.run_pre_tool_use(name, args)
                if hook_block is not None:
                    self.ui.tool_result(name, hook_block)
                    self._record_tool(tc, hook_block)
                    continue

                # Operator approval gate for mutating tools.
                if name in self._all_mutating and not self.auto_approve:
                    if not self.ui.approve(name, args):
                        result = ToolResult(
                            "operator denied this action. Choose a different "
                            "approach or ask the operator what they prefer.",
                            is_error=True)
                        self.ui.tool_result(name, result)
                        self._record_tool(tc, result)
                        continue

                # Show a ticking activity indicator during tool execution too —
                # a long `run` (pytest/build/deploy) must never look frozen.
                self.ui.thinking_start(label=("running" if name == "run" else name))
                try:
                    result = self._dispatch(name, args)
                finally:
                    self.ui.thinking_stop()

                # R-F4314 — PostToolUse hooks (e.g. the structural py_compile
                # check on write_file/edit_file). Surface any warnings.
                for warn in self.hooks.run_post_tool_use(name, args, result):
                    self.ui.info(f"[hook] {warn}")

                # R-F1045: enhance tool result with diff info for write/edit
                if name in ("write_file", "edit_file") and not result.is_error:
                    self.ui.tool_result(name, result)
                else:
                    self.ui.tool_result(name, result)
                self._record_tool(tc, result)

        msg = (f"Reached the per-turn tool-step limit ({self.max_steps}). Pausing, but "
               f"the full context is kept — resuming to keep going.")
        self.ui.info(msg)
        return TurnResult(final_text=msg, steps=steps, aborted=True, resumable=True)

    def _record_tool(self, tool_call: dict, result: ToolResult) -> None:
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call.get("id", ""),
            "content": result.output,
        })
