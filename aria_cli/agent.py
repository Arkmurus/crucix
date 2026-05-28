"""R-F988 — the agent loop for the ARIA Coder CLI.

Drives the LLM ↔ tool conversation: ask the model, execute any tool calls it
makes (gated by operator approval for mutating tools unless --auto), feed the
results back, and repeat until the model produces a final answer with no tool
calls. The transport-agnostic ``AgentUI`` lets the same loop back both the
interactive REPL and one-shot ``-p`` mode.
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
    def thinking_start(self) -> None: ...   # model call begins (show activity)
    def thinking_stop(self) -> None: ...    # model call returned

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


class Agent:
    def __init__(self, *, llm: LLMClient, toolbox: Toolbox, system_prompt: str,
                 ui: AgentUI, auto_approve: bool = False) -> None:
        self.llm = llm
        self.toolbox = toolbox
        self.ui = ui
        self.auto_approve = auto_approve
        self.retry_backoff = 2.0  # seconds, exponential; overridden to 0 in tests
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]

    def _chat_with_retry(self, steps: int):
        """Call the LLM, retrying transient failures with exponential backoff.
        Returns the LLMResponse, or a TurnResult on unrecoverable failure."""
        for attempt in range(LLM_MAX_ATTEMPTS):
            try:
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

    def run_turn(self, user_text: str) -> TurnResult:
        self.messages.append({"role": "user", "content": user_text})
        steps = 0
        while steps < MAX_STEPS:
            # Show "thinking" while we wait on the model so it's never silent.
            self.ui.thinking_start()
            try:
                resp = self._chat_with_retry(steps)
            finally:
                self.ui.thinking_stop()
            if isinstance(resp, TurnResult):  # unrecoverable / retries exhausted
                return resp

            # Echo the exact assistant message back into the history (preserves
            # tool_calls so the follow-up tool messages match).
            self.messages.append(resp.raw_message or {"role": "assistant", "content": resp.content})

            if resp.content:
                self.ui.assistant(resp.content)

            if not resp.tool_calls:
                return TurnResult(final_text=resp.content, steps=steps)

            for tc in resp.tool_calls:
                steps += 1
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except Exception:  # noqa: BLE001
                    args = {}
                    result = ToolResult(
                        f"error: could not parse arguments as JSON: {raw_args[:200]}",
                        is_error=True)
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

                result = self._dispatch(name, args)
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
