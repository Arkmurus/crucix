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
from typing import Tuple

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
    "deepseek-chat":            65536,
    "deepseek-reasoner":        65536,
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

# Default context window for unknown models — conservative
_DEFAULT_CONTEXT_WINDOW = 8192

# Tokens reserved for model output (subtracted from context window)
_RESERVED_OUTPUT_TOKENS = 4096

# Fallback: if the model name isn't in our table, use this
_FALLBACK_MODEL = "llama-3.3-70b-versatile"


def get_context_window(model: str) -> int:
    """Get the context window size for a given model.

    Args:
        model: Model name (e.g. "llama-3.3-70b-versatile", "deepseek-chat")

    Returns:
        Maximum total tokens the model supports.
    """
    # Try exact match first
    if model in _CONTEXT_WINDOWS:
        return _CONTEXT_WINDOWS[model]

    # Try prefix match (e.g. "gpt-4o-2024-08-06" matches "gpt-4o")
    for known, window in sorted(_CONTEXT_WINDOWS.items(), key=lambda x: -len(x[0])):
        if model.startswith(known):
            return window

    logger.debug("Unknown model '%s' — using default context window %d", model, _DEFAULT_CONTEXT_WINDOW)
    return _DEFAULT_CONTEXT_WINDOW


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
