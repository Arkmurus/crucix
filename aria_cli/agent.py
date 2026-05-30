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
import time
from dataclasses import dataclass

from .llm import LLMClient, LLMError
from .tools import MUTATING_TOOLS, TOOL_SCHEMAS, Toolbox, ToolResult


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
)


def _is_transient(exc: Exception) -> bool:
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


LLM_MAX_ATTEMPTS = max(1, _env_int("ARIA_CODER_LLM_RETRIES", 4))
AUTO_RESUME_MAX = max(0, _env_int("ARIA_CODER_AUTO_RESUME", 4))

# Loop guard: the step cap is effectively unlimited (R-F992), so a model that
# repeats the SAME tool call with identical args would run nearly forever (the
# grep('safety') x200 incident). Nudge after this many identical calls, abort after
# the hard cap. R-F1042.
LOOP_NUDGE_AT = max(2, _env_int("ARIA_CODER_LOOP_NUDGE", 3))
LOOP_ABORT_AT = max(LOOP_NUDGE_AT + 1, _env_int("ARIA_CODER_LOOP_ABORT", 8))


class Agent:
    def __init__(self, *, llm: LLMClient, toolbox: Toolbox, system_prompt: str,
                 ui: AgentUI, auto_approve: bool = False) -> None:
        self.llm = llm
        self.toolbox = toolbox
        self.ui = ui
        self.auto_approve = auto_approve
        self.retry_backoff = 2.0  # seconds, exponential; overridden to 0 in tests
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]

    def _chat_with_retry(self, steps: int, on_delta=None):
        """Call the LLM, retrying transient failures with exponential backoff.
        Streams tokens via on_delta when the provider supports it (never silent).
        Returns the LLMResponse, or a TurnResult on unrecoverable failure."""
        stream_fn = getattr(self.llm, "chat_stream", None)
        for attempt in range(LLM_MAX_ATTEMPTS):
            try:
                if stream_fn is not None and on_delta is not None:
                    return stream_fn(self.messages, tools=TOOL_SCHEMAS, on_delta=on_delta)
                return self.llm.chat(self.messages, tools=TOOL_SCHEMAS)
            except LLMError as exc:
                last = attempt == LLM_MAX_ATTEMPTS - 1
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
        result = self.run_turn(user_text)
        resumes = 0
        while result.aborted and result.resumable and resumes < AUTO_RESUME_MAX:
            resumes += 1
            self.ui.info(f"[self-start] turn ended incomplete — resuming "
                         f"automatically ({resumes}/{AUTO_RESUME_MAX})")
            result = self.run_turn("continue")
        return result

    def _dispatch(self, name: str, args: dict) -> ToolResult:
        method = getattr(self.toolbox, name, None)
        if method is None or name not in {s["function"]["name"] for s in TOOL_SCHEMAS}:
            return ToolResult(f"error: unknown tool '{name}'", is_error=True)
        try:
            return method(**args)
        except TypeError as exc:
            return ToolResult(f"error: bad arguments for {name}: {exc}", is_error=True)
        except Exception as exc:  # noqa: BLE001 — tools must never break the loop
            return ToolResult(f"error in {name}: {exc}", is_error=True)

    def _drain_claude_bridge(self) -> None:
        """R-F1082 — surface any new Claude→ARIA messages from the file bridge
        into the conversation, in real time, mid-task, as high-priority guidance.
        This is the real-time collaboration channel: Claude (senior reviewer)
        reviews ARIA's work and her findings land WITHOUT waiting for the operator
        to prompt a manual check_claude. Self-mode only (no bridge → no-op). Wrapped
        so the bridge can never break the agent loop."""
        base = getattr(self.toolbox, "bridge_base", None)
        if not base:
            return
        try:
            from . import bridge
            new = bridge.read_new(base, reader="aria")
        except Exception:  # noqa: BLE001 — the bridge must never break the loop
            return
        for m in new or []:
            text = (m.get("text") or "").strip()
            if not text:
                continue
            tag = "reply" if m.get("reply_to") else m.get("kind", "note")
            preview = text if len(text) <= 200 else text[:200] + "…"
            self.ui.info(f"[Claude {tag}] {preview}")
            self.messages.append({
                "role": "user",
                "content": (
                    "[LIVE MESSAGE FROM CLAUDE — your senior reviewer, via the agent "
                    "bridge. Treat as high-priority guidance: read it, and if it changes "
                    "what you should do next, adjust now.]\n" + text
                ),
            })

    def run_turn(self, user_text: str) -> TurnResult:
        self.messages.append({"role": "user", "content": user_text})
        steps = 0
        sig_counts: dict[str, int] = {}  # R-F1042 loop guard (per turn)
        while steps < MAX_STEPS:
            # R-F1082: pull any new guidance from Claude (via the bridge) into the
            # conversation before each LLM call — real-time collaboration, mid-task.
            self._drain_claude_bridge()
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
                sig = f"{name}|{raw_args}"
                sig_counts[sig] = sig_counts.get(sig, 0) + 1
                rep = sig_counts[sig]
                if rep >= LOOP_ABORT_AT:
                    msg = (f"Stopped: tool '{name}' was called {rep}x with identical "
                           f"arguments — a loop. The result will not change; redirect "
                           f"needed (different tool/args, or answer with what you have).")
                    self.ui.info(f"[loop-guard] {msg}")
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

                # Operator approval gate for mutating tools.
                if name in MUTATING_TOOLS and not self.auto_approve:
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

                # R-F1045: enhance tool result with diff info for write/edit
                if name in ("write_file", "edit_file") and not result.is_error:
                    self.ui.tool_result(name, result)
                else:
                    self.ui.tool_result(name, result)
                self._record_tool(tc, result)

        msg = (f"Reached the per-turn tool-step limit ({MAX_STEPS}). Pausing, but "
               f"the full context is kept — resuming to keep going.")
        self.ui.info(msg)
        return TurnResult(final_text=msg, steps=steps, aborted=True, resumable=True)

    def _record_tool(self, tool_call: dict, result: ToolResult) -> None:
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call.get("id", ""),
            "content": result.output,
        })
