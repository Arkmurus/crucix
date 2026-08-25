"""R-F1236 — Prompt Budget Manager.

Prevents HTTP 413 (Request Too Large) by estimating token counts and
truncating prompts before sending to the LLM provider.

No LLM dependency — pure character/word counting with provider-specific
context window limits. This is a structural guard, not a heuristic.

Usage:
    from .prompt_budget import enforce_budget, estimate_tokens

    system, user = enforce_budget(system_prompt, user_message, model="llama-3.3-70b-versatile")
    # system and user are now guaranteed to fit within the model's context window
"""
from __future__ import annotations

import logging
import os
from typing import Tuple
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.llm.prompt_budget")

# ── Provider context windows (tokens) ────────────────────────────────
# Source: provider documentation as of 2026-05.
# These are the MAXIMUM total tokens (system + user + output).
# We reserve 4096 tokens for output by default, so the prompt budget
# is context_window - reserved_output.

_CONTEXT_WINDOWS: dict[str, int] = {
    # Anthropic
    "claude-sonnet-4-6":        200000,
    "claude-3.5-haiku":         200000,
    "claude-3-opus":            200000,
    "claude-3-sonnet":          200000,
    "claude-3-haiku":           200000,
    # DeepSeek
    # R-F3045 (2026-07-25) — the v4 ids were MISSING here while R-F3032
    # migrated the live model to `deepseek-v4-flash`. Unknown model →
    # _DEFAULT_CONTEXT_WINDOW (8192) → prompt budget 4096, an 8-16x cut.
    # Measured live: ARIA_SYSTEM_PROMPT 83,519 chars was truncated to 7,059
    # (92% of her constitution DISCARDED) and a 418-char user message to 71,
    # ending mid-word at "...the Angol" — which is verbatim what she then
    # reported back ("your message cut off after 'Ango...'"). Every non-DD
    # LLM call ran that way from the migration until this fix.
    # R-F3629 (2026-08-01) — the v4 windows were understated 16x. MEASURED
    # against the live API from inside aria-intel, not read off a doc:
    #   flash: HTTP 200 at prompt_tokens=200,094 (so 65,536 was already false),
    #          then HTTP 400 "This model's maximum context length is 1048576
    #          tokens. However, you requested 1050110 tokens"
    #   pro:   HTTP 400 "...maximum context length is 1048565 tokens"
    # The API names its own ceiling in the 400 body, which is why an oversized
    # request is the cheapest honest probe: a rejected request bills nothing.
    # Note pro is ELEVEN tokens lower than flash — recorded exactly rather than
    # rounded, because this number is a truncation boundary and rounding UP
    # would 400 the very calls it exists to prevent.
    # deepseek-chat is RETIRED (R-F3032) and deepseek-reasoner was NOT probed —
    # both keep the old conservative value. Do not "tidy" them to 1M on the
    # strength of the two that were measured.
    "deepseek-chat":            65536,
    "deepseek-reasoner":        65536,
    "deepseek-v4-flash":      1048576,
    "deepseek-v4-pro":        1048565,
    # Groq
    "llama-3.3-70b-versatile":  131072,
    "llama-3.1-70b-versatile":  131072,
    "llama-3.1-8b-instant":     131072,
    "mixtral-8x7b-32768":      32768,
    "gemma2-9b-it":             8192,
    # OpenAI
    "gpt-4o":                   128000,
    "gpt-4o-mini":              128000,
    "gpt-4-turbo":              128000,
    "gpt-3.5-turbo":            16385,
    # Google
    "gemini-2.5-flash":         1048576,
    "gemini-2.0-flash":         1048576,
    "gemini-1.5-pro":           2097152,
    # Mistral
    "mistral-large-latest":     131072,
    "mistral-medium":           32768,
    # OpenRouter
    "openrouter/auto":          128000,
    # Ollama / local
    "llama3.1:8b":              131072,
    "llama3.3:70b":             131072,
    "qwen2.5:7b":               131072,
    "qwen2.5:32b":              131072,
    "mistral:7b":               32768,
    "phi3:14b":                 131072,
    # ARIA-LLM (sovereign)
    "aria-llm-v0.1":            32768,
}

# ── R-F3629 — CAPABILITY IS NOT PERMISSION ──────────────────────────────────
#
# The 65,536 above was WRONG as a capability number, and it was simultaneously
# doing a second, unstated job: capping spend. Correcting it to the measured
# 1,048,576 therefore raises the worst-case prompt of every DeepSeek call 16x
# — silently, as a side effect of a correctness fix, on the provider that
# serves nearly everything (§17: the $300/mo cap is real).
#
# That is the SAME defect R-F3627 just removed one module over: one integer
# doing two jobs, so tuning it for one purpose breaks the other. Fixing it here
# by leaving the window understated would keep the conflation and keep lying
# about the model; fixing it by raising the window alone would drop the cost
# guard entirely. So the two jobs are separated: `_CONTEXT_WINDOWS` states what
# the model CAN accept, and this states what we are WILLING TO SEND.
#
# Deliberately set far above any legitimate ARIA prompt — the constitution runs
# ~20k tokens, history is capped (R-F944), and an attached contract is ~15k — so
# this never binds on the normal path. It is a runaway guard (a pathological
# document, a history leak), not a routine constraint, and it logs at WARNING
# when it bites so the cause is visible rather than silently truncated away.
# Raise it with ARIA_MAX_PROMPT_TOKENS if a real workload ever needs more.
def _max_prompt_tokens() -> int:
    """Spend ceiling on prompt size, independent of model capability."""
    raw = os.getenv("ARIA_MAX_PROMPT_TOKENS", "").strip()
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            logger.warning("ARIA_MAX_PROMPT_TOKENS=%r is not an int — using default", raw)
    return _DEFAULT_MAX_PROMPT_TOKENS


_DEFAULT_MAX_PROMPT_TOKENS = 120_000

# Default context window for unknown models — conservative
_DEFAULT_CONTEXT_WINDOW = 8192

# Tokens reserved for model output (subtracted from context window)
_RESERVED_OUTPUT_TOKENS = 4096

# Fallback: if the model name isn't in our table, use this
_FALLBACK_MODEL = "llama-3.3-70b-versatile"


def _is_sovereign_model(model: str) -> bool:
    """True when ``model`` is ARIA's own served checkpoint.

    R-F4326 (C-274). Matches the configured ARIA_LLM_MODEL first (the exact
    string the server is told to serve), then the ``aria-llm`` family prefix so
    a checkpoint that has not reached the env var yet is still recognised.
    """
    m = (model or "").strip().lower()
    if not m:
        return False
    configured = (os.getenv("ARIA_LLM_MODEL") or "").strip().lower()
    return bool(configured and m == configured) or m.startswith("aria-llm")


def _declared_sovereign_window() -> int:
    """ARIA_LLM_MAX_MODEL_LEN as an int, or 0 when it declares nothing usable.

    R-F4326 (C-274). Returns 0 — never a guess — for unset, blank, non-numeric
    or non-positive values, so a typo in a secret falls back to the documented
    default instead of zeroing the budget and truncating every prompt to
    nothing.
    """
    try:
        v = int((os.getenv("ARIA_LLM_MAX_MODEL_LEN") or "").strip())
    except (TypeError, ValueError):
        return 0
    return v if v >= 512 else 0


@fail_wire(module="prompt_budget", gap_type="engine_failure")
def get_context_window(model: str) -> int:
    """Get the context window size for a given model.

    Args:
        model: Model name (e.g. "llama-3.3-70b-versatile", "deepseek-chat")

    Returns:
        Maximum total tokens the model supports.
    """
    # R-F4326 (C-274) — THE SOVEREIGN'S WINDOW COMES FROM THE MODEL, NOT A
    # VERSION LITERAL, and this branch runs FIRST so a stale table entry cannot
    # win over the served truth.
    #
    # Live 2026-08-25: "_CONTEXT_WINDOWS" held "aria-llm-v0.1": 32768 while the
    # served model was "aria-llm-v0.4-dpo". Exact match missed, the prefix pass
    # asked startswith("aria-llm-v0.1") -> False, and it fell through to
    # _DEFAULT_CONTEXT_WINDOW (8192) — a QUARTER of the real window. Every
    # sovereign prompt was truncated to a 7,392-token budget and the guard fired
    # on prompts that fit easily:
    #   "Even after truncation, prompt ~8495 tokens exceeds budget 7392 for
    #    model 'aria-llm-v0.4-dpo' — this should not happen"
    # The entry was right when written; a RENAME made it wrong, so no commit
    # diff ever showed it breaking.
    #
    # ARIA_LLM_MAX_MODEL_LEN is the authoritative statement of what vLLM serves
    # (it must match --max-model-len) and is already what aria_llm_provider
    # reads. Consulting it here removes the THIRD independent window for one
    # model — the same "there is ONE measure, do not fork it" rule §1/R-F2639
    # records, and that R-F4318/R-F4321 just applied to the CLI.
    #
    # Do NOT "fix" a future rename by adding another version literal here: that
    # greens today and rots at the next one. It is this defect, re-applied.
    if _is_sovereign_model(model):
        declared = _declared_sovereign_window()
        if declared:
            return declared
        # Nothing declares a window. Fall through to the table/default rather
        # than assuming a large one: an over-large budget posts a prompt the
        # server rejects with HTTP 400, which is the failure R-F4317 exists to
        # prevent. Under-reading truncates; over-reading breaks the call.

    # Try exact match first
    if model in _CONTEXT_WINDOWS:
        return _CONTEXT_WINDOWS[model]

    # Try prefix match (e.g. "gpt-4o-2024-08-06" matches "gpt-4o")
    for known, window in sorted(_CONTEXT_WINDOWS.items(), key=lambda x: -len(x[0])):
        if model.startswith(known):
            return window

    logger.debug("Unknown model '%s' — using default context window %d", model, _DEFAULT_CONTEXT_WINDOW)
    return _DEFAULT_CONTEXT_WINDOW


@fail_wire(module="prompt_budget", gap_type="engine_failure")
def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in a text string.

    Uses a fast approximation: ~4 characters per token for English text,
    ~1.5 characters per token for CJK characters, ~3 characters per token
    for mixed text. This is a conservative overestimate to avoid 413s.

    No LLM dependency — pure character counting.

    Args:
        text: The text to estimate

    Returns:
        Estimated token count (always >= 1)
    """
    if not text:
        return 0

    # Count CJK characters (Chinese, Japanese, Korean)
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u309f' or '\uac00' <= c <= '\ud7af')

    # Count the rest
    other_count = len(text) - cjk_count

    # CJK: ~1.5 chars per token, other: ~4 chars per token
    # Use conservative estimates (lower chars per token = higher token count)
    estimated = int(cjk_count / 1.2 + other_count / 3.0)

    return max(1, estimated)


@fail_wire(module="prompt_budget", gap_type="engine_failure")
def estimate_prompt_tokens(system_prompt: str, user_message: str) -> int:
    """Estimate total tokens for a prompt (system + user).

    Adds a small overhead for message formatting (~8 tokens).

    Args:
        system_prompt: The system prompt
        user_message: The user message

    Returns:
        Estimated total token count
    """
    return estimate_tokens(system_prompt) + estimate_tokens(user_message) + 8


@fail_wire(module="prompt_budget", gap_type="engine_failure")
def enforce_budget(
    system_prompt: str,
    user_message: str,
    *,
    model: str = _FALLBACK_MODEL,
    reserved_output: int = _RESERVED_OUTPUT_TOKENS,
    min_system_tokens: int = 100,
) -> Tuple[str, str]:
    """Enforce prompt budget by truncating if necessary.

    Ensures the total prompt (system + user) fits within the model's
    context window minus reserved output tokens. Truncates the user
    message first (preserving the system prompt), then the system
    prompt if still over budget.

    Args:
        system_prompt: The system prompt
        user_message: The user message
        model: Model name for context window lookup
        reserved_output: Tokens to reserve for model output
        min_system_tokens: Minimum tokens to keep in system prompt

    Returns:
        (system_prompt, user_message) — possibly truncated
    """
    context_window = get_context_window(model)
    prompt_budget = context_window - reserved_output

    # R-F3629 — the spend ceiling, applied AFTER the capability. Only ever
    # LOWERS, and only bites on a runaway prompt (see _max_prompt_tokens).
    _spend_cap = _max_prompt_tokens()
    if prompt_budget > _spend_cap:
        logger.debug(
            "Prompt budget for '%s' capped %d -> %d by ARIA_MAX_PROMPT_TOKENS "
            "(model capability is not the constraint here)",
            model, prompt_budget, _spend_cap,
        )
        prompt_budget = _spend_cap

    if prompt_budget <= 0:
        logger.warning(
            "Prompt budget <= 0 for model '%s' (window=%d, reserved=%d) — using minimum %d",
            model, context_window, reserved_output, _DEFAULT_CONTEXT_WINDOW // 2,
        )
        prompt_budget = _DEFAULT_CONTEXT_WINDOW // 2

    total_estimated = estimate_prompt_tokens(system_prompt, user_message)

    if total_estimated <= prompt_budget:
        return system_prompt, user_message

    logger.warning(
        "Prompt exceeds budget for '%s': ~%d tokens (budget=%d). Truncating...",
        model, total_estimated, prompt_budget,
    )

    # Calculate how many tokens we need to shed
    system_tokens = estimate_tokens(system_prompt)
    user_tokens = estimate_tokens(user_message)
    excess = total_estimated - prompt_budget

    # Strategy 1: Iteratively truncate user message to fit within budget
    # Keep system prompt unchanged, reduce user message until total fits
    target_total_tokens = prompt_budget - 8  # subtract overhead
    target_user_tokens = target_total_tokens - system_tokens
    if target_user_tokens < 50:
        target_user_tokens = 50  # keep at least 50 tokens for user message
    
    original_user_tokens = user_tokens
    truncated_user = user_message
    # Iterative truncation: reduce user text in chunks until it fits
    while estimate_tokens(truncated_user) + system_tokens + 8 > prompt_budget and len(truncated_user) > 0:
        # Truncate by removing roughly 20% of characters each iteration
        new_len = int(len(truncated_user) * 0.8)
        if new_len < 1:
            new_len = 0
        truncated_user = truncated_user[:new_len]
        # Try to break at a sentence boundary near the new length
        for separator in ("\n\n", ". ", "! ", "? ", "\n"):
            last = truncated_user.rfind(separator)
            if last > len(truncated_user) // 2:
                truncated_user = truncated_user[:last + len(separator)] + "..."
                break
        else:
            # No good break found, just keep what we have
            if truncated_user:
                truncated_user = truncated_user.rstrip() + "..."
            break
    
    new_user_tokens = estimate_tokens(truncated_user)
    tokens_shed = original_user_tokens - new_user_tokens
    logger.info(
        "Truncated user message from ~%d to ~%d tokens (shed %d tokens)",
        original_user_tokens, new_user_tokens, tokens_shed,
    )

    # Recalculate remaining budget
    remaining_excess = estimate_tokens(system_prompt) + estimate_tokens(truncated_user) + 8 - prompt_budget
    if remaining_excess > 0 and system_tokens > min_system_tokens:
        # Truncate system prompt as well
        target_system_chars = _tokens_to_chars(max(min_system_tokens, system_tokens - remaining_excess))
        truncated_system = _truncate_to_chars(system_prompt, target_system_chars)
        logger.info(
            "Truncated system from ~%d to ~%d tokens to fit budget",
            system_tokens, estimate_tokens(truncated_system),
        )
        new_total = estimate_tokens(truncated_system) + estimate_tokens(truncated_user) + 8
        if new_total > prompt_budget:
            logger.error(
                "Even after truncation, prompt ~%d tokens exceeds budget %d for model '%s' — this should not happen",
                new_total, prompt_budget, model,
            )
            # Fallback: truncate both to 50% each
            half_budget = prompt_budget // 2
            truncated_system = _truncate_to_chars(truncated_system, _tokens_to_chars(half_budget))
            truncated_user = _truncate_to_chars(truncated_user, _tokens_to_chars(half_budget))
        return truncated_system, truncated_user

    # Final check: if still over budget, force truncate system to half budget
    if estimate_tokens(system_prompt) + estimate_tokens(truncated_user) + 8 > prompt_budget:
        logger.error(
            "Prompt still exceeds budget after user truncation — forcing system truncation for model '%s'",
            model,
        )
        half_budget = prompt_budget // 2
        truncated_system = _truncate_to_chars(system_prompt, _tokens_to_chars(half_budget))
        truncated_user = _truncate_to_chars(truncated_user, _tokens_to_chars(half_budget))
        return truncated_system, truncated_user

    return system_prompt, truncated_user


def _tokens_to_chars(tokens: int) -> int:
    """Convert estimated token count to approximate character count.

    Uses the inverse of estimate_tokens: ~3.5 chars per token on average.
    """
    return max(1, int(tokens * 3.5))


def _truncate_to_chars(text: str, max_chars: int) -> str:
    """Truncate text to approximately max_chars, preserving whole sentences.

    Tries to break at a sentence boundary near the limit.
    """
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]

    # Try to break at a sentence boundary
    for separator in ("\n\n", ". ", "! ", "? ", "\n"):
        last = truncated.rfind(separator)
        if last > max_chars // 2:
            return truncated[:last + len(separator)] + "..."

    return truncated.rstrip() + "..."
