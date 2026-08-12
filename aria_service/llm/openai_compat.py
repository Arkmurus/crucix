"""
OpenAI-compatible provider — covers OpenAI, DeepSeek, Mistral, OpenRouter, MiniMax, Ollama.
All use the same /v1/chat/completions endpoint format.
"""
from __future__ import annotations

import os
import time
import httpx
import logging
from .provider import LLMProvider, LLMResult, ProviderError
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.llm.openai")


# R-F3032 (2026-07-25) — ONE source of truth for DeepSeek's model ids.
#
# DeepSeek RETIRED `deepseek-chat`. Live from inside aria-intel 2026-07-25:
#   HTTP 400 "The supported API model names are deepseek-v4-pro or
#             deepseek-v4-flash, but you passed deepseek-chat."
# DeepSeek is the PRIMARY provider, so that 400 was every non-DD LLM call:
# 258/258 failures, $0.00 spend for the day, and the autonomous engine firing
# 35 times an hour into a dead chain. A 400 is NOT retryable and no cooldown
# or retry can recover it — only a correct model id can.
#
# It was hardcoded in eight places (the chain entry, both factory paths,
# hybrid, this safe-default, the learning clients, the judge, the reviewer),
# so the obvious fix — "set the LLM_MODEL secret" — could not have reached
# most of them. Everything now resolves through these two functions, and they
# read the env at CALL time so a secret change needs no code deploy.
def default_deepseek_model() -> str:
    """Primary DeepSeek model. Cheap + fast; the everything-else workhorse."""
    return (os.getenv("ARIA_DEEPSEEK_CHAT_MODEL") or "deepseek-v4-flash").strip()


def deepseek_backup_enabled() -> bool:
    """Is the second DeepSeek chain slot wanted at all? (R-F3943)

    OPERATOR DIRECTIVE 2026-08-12: "just remove deepseek back up, we do not need
    a backup". DEFAULT OFF, and off is the honest default on the merits:

      * It is NOT redundancy. Both slots are built from the same
        `DEEPSEEK_API_KEY` on the same account (`verification_gate._vendor_of`
        exists precisely because treating them as two providers let a "second
        opinion" come from the same account). An account, key, billing or
        network failure takes both.
      * It costs ~3x the primary per token. Measured 2026-08-12 month-to-date:
        primary `deepseek-v4-flash` 126.5M tokens / $24.41 = **$0.193/M**;
        backup `deepseek-v4-pro` 17.1M tokens / $9.77 = **$0.572/M**. It served
        1,584 calls nobody asked it to serve.
      * It only ever guarded MODEL RETIREMENT (R-F3035) — a real event, but one
        that is loud (R-F3036 makes a dead chain loud) and fixed by changing
        one env var, not by paying 3x continuously to keep a warm spare.

    Re-enable deliberately with ARIA_DEEPSEEK_BACKUP_ENABLED=1. Only explicit
    truthy words count, so a typo cannot silently restore paid traffic — the
    inverse of `_dd_brave_only`'s default-ON reasoning, because here the safe
    default is the one that does NOT spend.
    """
    raw = (os.getenv("ARIA_DEEPSEEK_BACKUP_ENABLED") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def backup_deepseek_model() -> str:
    """A DIFFERENT DeepSeek model id, so retiring one cannot zero the chain
    (R-F3035). Same provider and key — this protects against a model
    retirement, which is what actually happened, not against an account or
    network failure.

    R-F3943 — returns "" when the backup slot is DISABLED (the default), which
    is what removes it from the chain. Note this function could not previously
    be turned off by configuration at all: `os.getenv(...) or "deepseek-v4-pro"`
    treats an EMPTY env var as unset, so `ARIA_DEEPSEEK_BACKUP_MODEL=""` still
    returned the hardcoded id. The only way to drop the slot was to set the
    backup model equal to the primary and rely on the `!=` test in
    `create_fallback_chain` — a coincidence, not a switch.
    """
    if not deepseek_backup_enabled():
        return ""
    return (os.getenv("ARIA_DEEPSEEK_BACKUP_MODEL") or "deepseek-v4-pro").strip()


# R-F2935 — per-provider known-safe default model, used only when the provider
# was misconfigured with a Claude id (see __init__). Mirrors the factory's own
# `model or "<default>"` fallbacks so a bad secret degrades to the SAME model the
# provider would use with no model configured at all. Unknown providers get ""
# (the API's own account default), never a claude id.
# R-F3032: the deepseek entry was `deepseek-chat` — a RETIRED id — so the
# rescue path degraded INTO the outage it exists to prevent. Read it through
# _safe_default_for(), which resolves deepseek at call time from the env.
_OPENAI_COMPAT_SAFE_DEFAULT: dict[str, str] = {
    "deepseek": "deepseek-v4-flash",
    "openai": "gpt-4",
    "groq": "llama-3.3-70b-versatile",
    "mistral": "mistral-large-latest",
    "minimax": "MiniMax-M2.5",
    "openrouter": "openrouter/auto",
    "ollama": "",
}


def _safe_default_for(name: str) -> str:
    """R-F3032 — the safe default, with DeepSeek resolved from the env so an
    operator can move off a retired model id without a code deploy."""
    if name == "deepseek":
        return default_deepseek_model()
    return _OPENAI_COMPAT_SAFE_DEFAULT.get(name, "")


# R-F3591 — above this, `reasoning_content` is DELIBERATION, not an answer.
# Live leak was 2K+ chars of internal monologue published verbatim; R-F3033's
# legitimate case ("The answer is 42.") is 18. 600 sits far from both.
_REASONING_ANSWER_MAX = 600


# ── R-F3607 — A REASONING MODEL MUST BE ABLE TO AFFORD ITS OWN REASONING ─────
#
# On a reasoning model `max_tokens` covers reasoning_content + content, and the
# reasoning is emitted FIRST. So a budget large enough for an answer on a
# classic model can be too small to produce ANY answer here: the reasoning
# consumes all of it, `content` returns empty with finish_reason='length', and
# R-F3591 (correctly) refuses to serve the chain of thought.
#
# That is what took chat down on 2026-08-01 — an 800-token cap intended for the
# sovereign 7B reached deepseek-v4-flash, whose reasoning alone ran 3455 chars
# (~800 tokens). Both the primary AND the backup model inherited the same cap,
# so the whole chain failed identically and the fallback could not save it.
#
# R-F3606 fixes the caller that set 800. This is the STRUCTURAL guarantee that
# no future caller can reintroduce the class: the floor is enforced at the one
# place the request is built, so it covers every caller of every reasoning
# model — including small-budget helpers like llm/structured.py (max_tokens
# default 1000), which would fail the same way and has never been exercised
# against a reasoning model.
#
# It only ever RAISES a budget, and max_tokens is a ceiling rather than a
# target, so a model that needs fewer tokens still returns early and costs the
# same. The sovereign is exempt (handled by clamp_for_sovereign) — it is not a
# reasoning model and has the opposite, latency-driven constraint.
_REASONING_MIN_COMPLETION_TOKENS = 2048


# ── R-F3627 — THE FLOOR WAS STILL A CAP ON THINKING + ANSWER ─────────────────
#
# R-F3607 (above) raised a too-small budget to a floor. That closed the 800-token
# case and NOTHING MORE, because the quantity it floors is still the COMBINED
# reasoning+content budget. The caller is expressing "how long an ANSWER do I
# want"; the model spends that same allowance thinking first. So every floor is
# just a bigger cliff, and the failure recurs at the new number.
#
# Live 2026-08-01, ~6 hours after R-F3606 raised chat 800 -> 4000:
#   [deepseek_backup] reasoning consumed the token budget
#   (model=deepseek-v4-pro, finish_reason=length, reasoning=13527 chars)
# 13,527 chars / 4,000 tokens = 3.38 chars-per-token — the ENTIRE raised budget,
# spent thinking, content empty. The same arithmetic that proved R-F3606's
# diagnosis (3,455 chars on an 800 cap) now disproves its remedy. Note both
# samples are CONDITIONED ON FAILURE: a turn whose reasoning ended early never
# raises, so these observations are a lower bound on how long the model thinks,
# never evidence that it stops at some number.
#
# The structural fix is to stop treating one integer as two things. On a
# reasoning model the caller's `max_tokens` is RESERVED for the answer, and
# headroom for the thinking is added ON TOP. DeepSeek V4 carries a 1M context
# (vendored vendor doc: awesome-deepseek-agent-main/docs/cherry_studio.md:41),
# and `max_tokens` is a ceiling the model stops short of, so headroom on an easy
# turn is billed at exactly zero. Reasoning effort itself is NOT a lever here:
# DeepSeek exposes only `high` and `max` (docs/oh-my-pi.md:93), so the model
# cannot be asked to think less — only to be given room to finish.
_REASONING_HEADROOM_TOKENS = 8192

# The escalation ceiling. Bounds the retry below so a pathological turn cannot
# walk the budget up indefinitely, and keeps the total inside the smallest
# context window this file records for a reasoning model (prompt_budget.py).
_REASONING_MAX_COMPLETION_TOKENS = 32768

# Substrings of model ids that emit `reasoning_content` before the answer.
# Kept as fragments so a point release (deepseek-v4-pro-0801) is still matched.
_REASONING_MODEL_MARKERS = ("deepseek-v4", "deepseek-reasoner", "o1-", "o3-")

# R-F3627 — the ProviderError kind for "the budget died during deliberation".
# A distinct kind is what lets the ONE escalation below fire on the one failure
# it can actually cure, and nothing else. fallback.py classifies only
# ("auth", "billing") and "rate_limit" specially, so this lands in the same soft
# branch "other" already did — no cooldown behaviour changes.
KIND_REASONING_TRUNCATED = "reasoning_truncated"

# R-F3629 — below this many seconds left on the caller's clock, the escalation
# is not started at all. A larger budget takes LONGER to generate, so a retry
# squeezed into the last moments is the least likely of all to succeed.
_MIN_RETRY_SECONDS = 15.0


def _is_reasoning_model(model: str | None) -> bool:
    """True when `model` spends part of max_tokens on reasoning before content."""
    m = (model or "").strip().lower()
    return any(marker in m for marker in _REASONING_MODEL_MARKERS)


def _floor_completion_budget(model: str | None, max_tokens: int, *, attempt: int = 0) -> int:
    """The wire budget for `max_tokens` worth of ANSWER from `model`.

    Classic model: unchanged — its answer is the whole completion, and raising
    the ceiling would be an unrequested cost increase.

    Reasoning model: `max_tokens` is reserved for the answer and reasoning
    headroom is added on top, so the answer cannot be crowded out by the
    thinking that precedes it (R-F3627). `attempt` doubles the headroom for the
    single escalation retry in complete(). Never lowers; never exceeds
    _REASONING_MAX_COMPLETION_TOKENS.
    """
    if not _is_reasoning_model(model):
        return max_tokens
    try:
        n = int(max_tokens)
    except (TypeError, ValueError):
        n = _REASONING_MIN_COMPLETION_TOKENS
    if n < _REASONING_MIN_COMPLETION_TOKENS:
        n = _REASONING_MIN_COMPLETION_TOKENS
    headroom = _REASONING_HEADROOM_TOKENS * (2 ** max(0, int(attempt)))
    total = min(n + headroom, _REASONING_MAX_COMPLETION_TOKENS)
    # Never lower what the caller asked for, even at the ceiling.
    return max(total, n)

class OpenAICompatProvider(LLMProvider):
    """Generic OpenAI-compatible chat completions provider."""

    def __init__(
        self,
        *,
        name: str = "openai",
        api_key: str = "",
        model: str = "gpt-4",
        base_url: str = "https://api.openai.com/v1",
        extra_headers: dict | None = None,
        default_timeout: float = 60.0,
    ):
        self.name = name
        # R-F2935 — an OpenAI-compatible endpoint (DeepSeek/OpenAI/Groq/...) can
        # never serve a Claude model. A claude-* id lands here when the provider
        # is MISCONFIGURED with one as its default — which is exactly what
        # happened on the 2026-07-23 restructure: LLM_PROVIDER=deepseek with
        # LLM_MODEL=claude-opus-4-8 made main.py build the DeepSeek PRIMARY with
        # self._model="claude-opus-4-8", so every call with no per-call override
        # sent claude-opus-4-8 to api.deepseek.com → HTTP 400 → cooldown →
        # self_improve/DD layers degraded to local_brain. The per-call override
        # was already guarded; the CONFIGURED default was not. Refuse it at
        # construction and fall back to this provider's known-safe default, once,
        # loudly — so a bad secret degrades to a working model instead of a 400
        # storm.
        _model = model
        if _model and str(_model).startswith("claude") and name != "anthropic":
            _safe = _safe_default_for(name)
            logger.warning(
                "[openai_compat] %s configured with a Claude model %r — it "
                "cannot serve Claude; using %r instead. Fix the secret "
                "(a non-anthropic provider's model must not be a claude id).",
                name, _model, _safe or "<none>",
            )
            _model = _safe
        self._api_key = api_key
        self._model = _model
        self._base_url = base_url.rstrip("/")
        self._extra_headers = extra_headers or {}
        self._default_timeout = default_timeout

    @property
    def is_configured(self) -> bool:
        # Ollama doesn't need an API key
        if self.name == "ollama":
            return bool(self._model)
        return bool(self._api_key)

    def _resolve_completion_budget(
        self, eff_model: str | None, max_tokens: int, *, attempt: int = 0,
    ) -> int:
        """R-F3607 — the completion budget the model that ACTUALLY serves can
        work with. One place, so complete() and the inherited stream() (§13)
        cannot drift.

        R-F3627 — `attempt` selects the escalation rung. LLMProvider.stream()
        delegates to complete() (provider.py:127) and this class does not
        override it, so the streaming fork inherits the whole treatment —
        budget AND retry — with no second implementation to drift.

        Deliberately does NOT apply the sovereign ceiling, even though the
        chain can hold a sovereign slot named 'aria_llm' under SHADOW /
        PRIMARY_ALL. That chain serves the self-coder too, and capping it at 800
        would truncate generated code (R-F904 / §21c). The sovereign's chat
        ceiling is applied by model_router, which knows the call is chat.
        """
        return _floor_completion_budget(eff_model, max_tokens, attempt=attempt)

    @fail_wire(module="openai_compat", gap_type="engine_failure", control_flow_exempt=("ProviderError",))
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 0,
        model: str | None = None,   # R-F2768 — accept the routing override (a Claude id is ignored)
    ) -> LLMResult:
        """R-F3627 — at most TWO attempts, and only ever for the one failure a
        second attempt can cure.

        This is deliberately NOT a retry count. Retrying a truncated reasoning
        turn on the NEXT PROVIDER — which is all the chain could do before — is
        structurally incapable of succeeding: fallback.py forwards the caller's
        max_tokens unchanged, so the backup model inherits the identical budget
        and dies at the identical point. That is exactly what the 2026-08-01
        page recorded (attempts=1, both DeepSeek entries listed, chain
        exhausted). The escalation below retries the SAME provider with the one
        parameter the error itself names as deficient, once, bounded by
        _REASONING_MAX_COMPLETION_TOKENS — a root-cause correction of the
        request, not a hope that the same request fares better.
        """
        timeout = timeout or self._default_timeout

        # R-F2768 — accept the per-call routing override but NEVER send a Claude
        # model id to an OpenAI-compatible API (it would 400). A non-Claude
        # override (e.g. an explicit OpenAI model) is honoured; else configured.
        # R-F3606: resolved HERE (was below) so the budget rules and the R-F1236
        # prompt budget both see the model that will actually serve.
        _eff_model = model if (model and not str(model).startswith("claude")) else self._model

        # R-F3629 — THE ESCALATION SHARES THE CALLER'S CLOCK, IT DOES NOT DOUBLE IT.
        #
        # R-F3627 gave each attempt the full `timeout`, so complete() could take
        # 2x what the caller asked for. `timeout` is a contract — the chain sizes
        # its own per-provider budget from it (fallback.py `per_call`) and callers
        # above set it against a user-facing deadline. A retry that silently
        # doubles the wall clock is a second defect wearing the first one's fix.
        _deadline = time.monotonic() + timeout
        # Only ever read on attempt 1, which is reachable only via the except
        # branch that sets it. Initialised anyway so a future edit to the loop
        # cannot turn a provider failure into a NameError.
        _last_truncation: ProviderError | None = None
        for _attempt in (0, 1):
            _budget = self._resolve_completion_budget(
                _eff_model, max_tokens, attempt=_attempt,
            )
            _remaining = _deadline - time.monotonic()
            if _attempt > 0:
                # Nothing left worth dialling for. Mirrors the chain's own
                # _PROVIDER_MIN_BUDGET reasoning: a call that cannot finish is
                # worse than an honest failure, because it burns the deadline
                # and still returns nothing.
                if _remaining < _MIN_RETRY_SECONDS and _last_truncation is not None:
                    logger.warning(
                        "[R-F3629] %s (%s) reasoned past its budget, but only "
                        "%.1fs of the caller's %.1fs timeout remains — failing "
                        "honestly rather than starting a call that cannot finish.",
                        self.name, _eff_model, _remaining, timeout,
                    )
                    raise _last_truncation
            try:
                return await self._one_completion(
                    system_prompt, user_message,
                    eff_model=_eff_model, max_tokens=_budget,
                    timeout=max(_remaining, _MIN_RETRY_SECONDS),
                )
            except ProviderError as e:
                _curable = (
                    getattr(e, "kind", "") == KIND_REASONING_TRUNCATED
                    and _attempt == 0
                    # A second call is pointless once the ceiling is already the
                    # budget — nothing would change about the request.
                    and _budget < _REASONING_MAX_COMPLETION_TOKENS
                )
                if not _curable:
                    raise
                _last_truncation = e
                logger.warning(
                    "[R-F3627] %s (%s) spent its %d-token budget reasoning; "
                    "retrying ONCE with doubled headroom (%.1fs of %.1fs left). %s",
                    self.name, _eff_model, _budget,
                    _deadline - time.monotonic(), timeout, e,
                )
        # Unreachable: the loop either returns or raises on the final attempt.
        raise ProviderError(self.name, "completion escalation fell through", kind="other")

    async def _one_completion(
        self,
        system_prompt: str,
        user_message: str,
        *,
        eff_model: str | None,
        max_tokens: int,
        timeout: float,
    ) -> LLMResult:
        """ONE request/response cycle at a FIXED budget (R-F3627).

        Extracted from complete() so both escalation attempts run byte-identical
        logic. Deliberately not decorated with @fail_wire: complete() carries the
        wire, and wiring here as well would file two gaps for one user-visible
        failure and count the curable first attempt as an outage.
        """
        _eff_model = eff_model

        # R-F1236: Enforce prompt budget before sending — prevents HTTP 413
        # (Request Too Large) on models with smaller context windows.
        # R-F3627 — pass the EFFECTIVE model. R-F3606 resolved _eff_model early
        # so "the R-F1236 prompt budget [sees] the model that will actually
        # serve", but this call still read self._model, so a per-call routing
        # override was budgeted against the configured model's window instead of
        # the serving one. The reserve is now also materially larger, which is
        # what makes the mismatch worth correcting rather than noting.
        try:
            from .prompt_budget import enforce_budget
            system_prompt, user_message = enforce_budget(
                system_prompt, user_message,
                model=_eff_model or self._model,
                reserved_output=max_tokens,
            )
        except Exception:
            logger.debug("[prompt_budget] enforce_budget failed (non-fatal)", exc_info=True)

        headers = {"Content-Type": "application/json", **self._extra_headers}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        # (_eff_model resolved above — R-F3606.)
        payload = {
            "model": _eff_model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code >= 400:
                    body = ""
                    try:
                        body = resp.text[:300]
                    except Exception:
                        pass
                    # R-F3036 — an HTTP 4xx/5xx is the single most common
                    # provider failure and was the ONLY one that emitted
                    # nothing: this method is decorated
                    # `@fail_wire(..., control_flow_exempt=("ProviderError",))`
                    # and the explicit wire_failure calls below cover only
                    # httpx timeouts and network errors. That exemption is
                    # correct for the GAP (the chain may still recover via a
                    # fallback, so it is control flow, not a capability gap)
                    # but it must not also suppress the HEALTH signal — a
                    # 100%-failing provider has to be visible whether or not
                    # something else covered for it. This is what let the
                    # 2026-07-25 deepseek-chat 400 storm run unseen.
                    try:
                        from ..intel.engine_wiring import wire_failure as _wf
                        _wf(
                            module=f"llm_{self.name}",
                            detail=(f"Provider {self.name} HTTP {resp.status_code} "
                                    f"(model={_eff_model}): {body[:200]}"),
                            gap_type="llm_provider_failure",
                            source=f"llm_{self.name}",
                        )
                    except Exception:
                        pass
                    raise ProviderError.from_http_status(self.name, resp.status_code, body)
                data = resp.json()
        except ProviderError:
            raise
        except httpx.TimeoutException as e:
            # R-F1059 — wire timeout to brain
            try:
                from ..intel.engine_wiring import wire_failure as _wf
                _wf(
                    module=f"llm_{self.name}",
                    detail=f"Provider {self.name} timeout: {e}",
                    gap_type="llm_provider_failure",
                    source=f"llm_{self.name}",
                )
            except Exception:
                pass
            raise ProviderError(self.name, "timeout", kind="timeout", retryable=True, cause=e)
        except httpx.HTTPError as e:
            # R-F1059 — wire network error to brain
            try:
                from ..intel.engine_wiring import wire_failure as _wf
                _wf(
                    module=f"llm_{self.name}",
                    detail=f"Provider {self.name} network error: {e}",
                    gap_type="llm_provider_failure",
                    source=f"llm_{self.name}",
                )
            except Exception:
                pass
            raise ProviderError(self.name, f"network error: {e}", kind="other", retryable=True, cause=e)

        choice = data.get("choices", [{}])[0]
        usage = data.get("usage", {})
        _msg = choice.get("message", {}) or {}

        # R-F3033 (2026-07-25) — REASONING models split their output.
        # deepseek-v4-flash / -pro (and the o1-style OpenAI-compatible models)
        # emit the chain of thought into `reasoning_content` and the answer
        # into `content`. When the token budget is tight the reasoning
        # consumes it and `content` comes back EMPTY on an HTTP 200.
        #
        # Verified live 2026-07-25 against the production key:
        #   max_tokens=16  -> content:""     reasoning_content:"We need to re…"
        #   max_tokens=600 -> content:"OK."  reasoning_content:"We are asked…"
        #
        # Reading `content` alone therefore returns LLMResult(text="") with no
        # error — a SILENT false success. The fallback chain books it as a
        # success, stops, and hands the caller an empty answer; on a DD that
        # is an empty section rendered as though the model had nothing to say.
        # That is strictly worse than the HTTP 400 this migration replaces,
        # so it has to be caught here, at the ONE place the wire is parsed.
        _text = (_msg.get("content") or "").strip()

        # ── R-F3591 — REASONING IS A DIAGNOSTIC, NEVER THE ANSWER ────────────
        #
        # R-F3033 correctly identified that an empty `content` on an HTTP 200 was
        # being booked as a silent success, and fixed it by SERVING
        # `reasoning_content` instead. That trades a silent empty answer for a
        # leaked one, and the leak is worse.
        #
        # Live 2026-07-31, operator asked "What is the time in Portugal?" and
        # received ARIA's raw chain of thought: "The user asks... I need to answer
        # from the snippets only. Let me look at what the snippets actually
        # contain... But wait — can I use that?" — truncated mid-word at
        # "CONFIRMED (W" because the budget ran out DURING the reasoning. It
        # exposed the prompt's internals (ANSWER SCOPE, snippet numbering, the
        # grounding rules) to a user, and never answered the question.
        #
        # Empty content is still detected, and still fails loudly — that half of
        # R-F3033 stands. What changes is that the failure raises a RETRYABLE
        # error so the chain retries or falls to the next provider, instead of
        # publishing deliberation as prose. The reasoning is logged (truncated)
        # for diagnosis, where it belongs.
        _reasoning = (_msg.get("reasoning_content") or "").strip()
        if not _text and _reasoning:
            _fr_reason = choice.get("finish_reason") or "unknown"
            # TRUNCATED, or long enough to be DELIBERATION rather than an answer.
            #
            # R-F3033's fixture is the legitimate case this must preserve: a model
            # that puts a short, complete answer ("The answer is 42.") in
            # reasoning_content and nothing in content. Raising on that would
            # throw away a good answer.
            #
            # The live leak was the opposite: 2K+ characters of "The user asks…
            # Let me look at what the snippets actually contain… But wait — can I
            # use that?", cut mid-word because the budget ran out. finish_reason
            # =='length' proves truncation; length alone catches an untruncated
            # ramble. Either way it is deliberation, and deliberation is never the
            # answer.
            _truncated = str(_fr_reason).lower() == "length"
            if _truncated or len(_reasoning) > _REASONING_ANSWER_MAX:
                logger.warning(
                    "[R-F3591] %s (%s) spent its budget reasoning: content EMPTY, "
                    "reasoning_content %d chars, finish_reason=%s. Failing over "
                    "rather than serving the chain of thought. Head: %.300s",
                    self.name, _eff_model, len(_reasoning), _fr_reason, _reasoning,
                )
                raise ProviderError(
                    self.name,
                    (f"reasoning consumed the token budget (model={_eff_model}, "
                     f"finish_reason={_fr_reason}, reasoning={len(_reasoning)} chars, "
                     f"max_tokens={max_tokens}) — no answer was produced."),
                    # R-F3627 — a DISTINCT kind. This failure is a request-shape
                    # problem on a healthy, paid, reachable provider, and it is
                    # the only one the escalation in complete() can cure. Telling
                    # it apart from generic "other" is what stops the chain from
                    # burning its fallback on a budget the fallback also inherits.
                    kind=KIND_REASONING_TRUNCATED,
                    retryable=True,
                )
            # Short and complete: the model simply used the other field.
            _text = _reasoning
            logger.info(
                "[R-F3033] %s (%s) returned empty content; using a SHORT complete "
                "reasoning_content (%d chars) as the answer.",
                self.name, _eff_model, len(_reasoning),
            )

        if not _text:
            # Nothing usable anywhere. RAISE rather than return "" so the
            # chain treats it as a failure and tries the next provider,
            # instead of recording a success that produced no output.
            _fr = choice.get("finish_reason") or ""
            raise ProviderError(
                self.name,
                (f"empty response (model={_eff_model}, finish_reason={_fr or 'unknown'}, "
                 f"completion_tokens={usage.get('completion_tokens', 0)}) — no content "
                 f"and no reasoning_content"),
                kind="other",
                retryable=True,
            )

        return LLMResult(
            text=_text,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            model=data.get("model", self._model),
        )
