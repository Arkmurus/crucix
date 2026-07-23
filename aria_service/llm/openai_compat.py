"""
OpenAI-compatible provider — covers OpenAI, DeepSeek, Mistral, OpenRouter, MiniMax, Ollama.
All use the same /v1/chat/completions endpoint format.
"""
from __future__ import annotations

import httpx
import logging
from .provider import LLMProvider, LLMResult, ProviderError
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.llm.openai")

# R-F2935 — per-provider known-safe default model, used only when the provider
# was misconfigured with a Claude id (see __init__). Mirrors the factory's own
# `model or "<default>"` fallbacks so a bad secret degrades to the SAME model the
# provider would use with no model configured at all. Unknown providers get ""
# (the API's own account default), never a claude id.
_OPENAI_COMPAT_SAFE_DEFAULT: dict[str, str] = {
    "deepseek": "deepseek-chat",
    "openai": "gpt-4",
    "groq": "llama-3.3-70b-versatile",
    "mistral": "mistral-large-latest",
    "minimax": "MiniMax-M2.5",
    "openrouter": "openrouter/auto",
    "ollama": "",
}


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
            _safe = _OPENAI_COMPAT_SAFE_DEFAULT.get(name, "")
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

        return LLMResult(
            text=choice.get("message", {}).get("content", ""),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            model=data.get("model", self._model),
        )
