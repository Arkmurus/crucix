"""
OpenAI-compatible provider — covers OpenAI, DeepSeek, Mistral, OpenRouter, MiniMax, Ollama.
All use the same /v1/chat/completions endpoint format.
"""
from __future__ import annotations

import os
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


def backup_deepseek_model() -> str:
    """A DIFFERENT DeepSeek model id, so retiring one cannot zero the chain
    (R-F3035). Same provider and key — this protects against a model
    retirement, which is what actually happened, not against an account or
    network failure."""
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
        timeout = timeout or self._default_timeout

        # R-F1236: Enforce prompt budget before sending — prevents HTTP 413
        # (Request Too Large) on models with smaller context windows.
        try:
            from .prompt_budget import enforce_budget
            system_prompt, user_message = enforce_budget(
                system_prompt, user_message,
                model=self._model,
                reserved_output=max_tokens,
            )
        except Exception:
            logger.debug("[prompt_budget] enforce_budget failed (non-fatal)", exc_info=True)

        headers = {"Content-Type": "application/json", **self._extra_headers}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        # R-F2768 — accept the per-call routing override but NEVER send a Claude
        # model id to an OpenAI-compatible API (it would 400). A non-Claude
        # override (e.g. an explicit OpenAI model) is honoured; else configured.
        _eff_model = model if (model and not str(model).startswith("claude")) else self._model
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
        if not _text:
            _text = (_msg.get("reasoning_content") or "").strip()
            if _text:
                logger.info(
                    "[R-F3033] %s (%s) returned empty content; using "
                    "reasoning_content (%d chars). Raise max_tokens to give "
                    "the answer room after the reasoning.",
                    self.name, _eff_model, len(_text),
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
