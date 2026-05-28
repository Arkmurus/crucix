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

    def approve(self, name: str, args: dict) -> bool:
        """Return True to allow a mutating tool call. Default: allow."""
        return True


@dataclass
class TurnResult:
    final_text: str
    steps: int
    aborted: bool = False


class Agent:
    def __init__(self, *, llm: LLMClient, toolbox: Toolbox, system_prompt: str,
                 ui: AgentUI, auto_approve: bool = False) -> None:
        self.llm = llm
        self.toolbox = toolbox
        self.ui = ui
        self.auto_approve = auto_approve
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]

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
            try:
                resp = self.llm.chat(self.messages, tools=TOOL_SCHEMAS)
            except LLMError as exc:
                self.ui.info(f"[llm error] {exc}")
                return TurnResult(final_text=f"LLM error: {exc}", steps=steps, aborted=True)

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

        msg = (f"Reached the per-turn tool-step limit ({MAX_STEPS}). I paused here, "
               f"but the full context is kept — say 'continue' to resume, or break "
               f"the task into smaller steps.")
        self.ui.info(msg)
        return TurnResult(final_text=msg, steps=steps, aborted=True)

    def _record_tool(self, tool_call: dict, result: ToolResult) -> None:
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call.get("id", ""),
            "content": result.output,
        })
