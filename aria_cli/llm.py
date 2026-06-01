"""R-F988 — LLM client for the ARIA Coder CLI.

A thin, synchronous OpenAI-compatible chat client with native tool/function
calling. DeepSeek is the default backend (the only active provider per
CLAUDE.md §18), but any OpenAI-compatible endpoint works (openai, groq,
openrouter, mistral, ollama) by setting the provider/base-url env vars.

We do NOT reuse ``aria_service.llm.OpenAICompatProvider`` here because that
abstraction is plain text-in/text-out (no ``tools`` array, no ``tool_calls``
parsing) — an agent loop needs function calling. The env-var names and base-url
defaults are kept identical to ``aria_service.llm.factory`` for consistency.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import httpx

# Base-url defaults mirror aria_service/llm/factory.py so the CLI talks to the
# same endpoints ARIA already uses.
_PROVIDER_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "mistral": "https://api.mistral.ai/v1",
    "aria": "",  # base_url is set dynamically from ARIA_SERVICE_URL
}
_PROVIDER_DEFAULT_MODELS = {
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "openrouter/auto",
    "mistral": "mistral-large-latest",
    "ollama": "llama3.1:8b",
    "aria": "aria-coder",
}
# R-F1280: which env var holds the credential for each provider. Used so the CLI
# sends the RIGHT key to the selected provider — never ARIA's internal token to
# an external API (that 401'd every DeepSeek call).
_PROVIDER_KEY_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "aria": "ARIA_INTERNAL_TOKEN",
}


class LLMError(RuntimeError):
    """Raised when the LLM endpoint is unreachable or returns an error."""


@dataclass
class LLMConfig:
    provider: str = "aria"
    api_key: str = ""
    model: str = "aria-coder"
    base_url: str = ""
    timeout: float = 30.0
    max_tokens: int = 8192
    temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Resolve config from env. ARIA_CODER_* overrides, then the generic
        LLM_*/DEEPSEEK_* vars that the rest of the stack already uses.

        Default provider is now ``aria`` — the ARIA server's own LLM endpoint
        (/api/aria/coder/llm). Falls back to deepseek if ARIA_SERVICE_URL is
        not set or the aria provider is explicitly overridden.
        """
        provider = (
            os.getenv("ARIA_CODER_LLM_PROVIDER")
            or os.getenv("LLM_PROVIDER")
            or "aria"
        ).strip().lower()

        # R-F1280: api-key resolution MUST be provider-aware. The old order put
        # ARIA_INTERNAL_TOKEN ahead of the provider-specific key, so with
        # provider=deepseek the CLI sent ARIA's *internal* token to DeepSeek's
        # API and every call 401'd ("api key ...9c2a is invalid"). Pick the key
        # that belongs to the selected provider; ARIA_INTERNAL_TOKEN is only the
        # right credential for the in-house `aria` provider.
        _provider_key = os.getenv(_PROVIDER_KEY_ENV.get(provider, ""), "") if provider else ""
        api_key = (
            os.getenv("ARIA_CODER_LLM_API_KEY")
            or os.getenv("LLM_API_KEY")
            or _provider_key
            or ""
        ).strip()

        model = (
            os.getenv("ARIA_CODER_LLM_MODEL")
            or os.getenv("LLM_MODEL")
            or _PROVIDER_DEFAULT_MODELS.get(provider, "aria-coder")
        ).strip()

        ollama_url = (os.getenv("OLLAMA_URL") or "http://localhost:11434").rstrip("/")

        if provider == "aria":
            # ARIA server provider: use the server's own LLM endpoint
            aria_url = (os.getenv("ARIA_SERVICE_URL") or "http://localhost:8000").rstrip("/")
            base_url = f"{aria_url}/api/aria"
            # Use ARIA_INTERNAL_TOKEN for auth
            if not api_key:
                api_key = os.getenv("ARIA_INTERNAL_TOKEN", "")
        else:
            base_url = (
                os.getenv("ARIA_CODER_LLM_BASE_URL")
                or os.getenv("OPENAI_BASE_URL")
                or (f"{ollama_url}/v1" if provider == "ollama"
                    else _PROVIDER_BASE_URLS.get(provider, "https://api.deepseek.com/v1"))
            ).rstrip("/")

        timeout = float(os.getenv("ARIA_CODER_LLM_TIMEOUT", "30"))
        max_tokens = int(os.getenv("ARIA_CODER_LLM_MAX_TOKENS", "8192"))
        return cls(
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            max_tokens=max_tokens,
        )

    @property
    def is_configured(self) -> bool:
        # Ollama (local) needs no key; everything else does.
        return self.provider == "ollama" or bool(self.api_key)


@dataclass
class LLMResponse:
    """One assistant turn. ``tool_calls`` is the OpenAI-shaped list (each item
    has ``id`` and ``function.name`` / ``function.arguments``)."""
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    raw_message: dict = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient:
    """Synchronous OpenAI-compatible chat-completions client with tools."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.from_env()
        self._client = httpx.Client(timeout=self.config.timeout)
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        # ARIA provider uses the server's /api/aria/chat endpoint
        if self.config.provider == "aria":
            return self._aria_chat(messages)

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload: dict = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        url = f"{self.config.base_url}/chat/completions"
        try:
            resp = self._client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach LLM endpoint {url}: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMError(
                f"LLM endpoint {url} returned HTTP {resp.status_code}: "
                f"{resp.text[:500]}"
            )

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"LLM endpoint returned non-JSON: {resp.text[:500]}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"LLM endpoint returned no choices: {json.dumps(data)[:500]}")

        message = choices[0].get("message") or {}
        usage = data.get("usage") or {}
        in_tok = int(usage.get("prompt_tokens", 0) or 0)
        out_tok = int(usage.get("completion_tokens", 0) or 0)
        self.total_input_tokens += in_tok
        self.total_output_tokens += out_tok

        return LLMResponse(
            content=message.get("content") or "",
            tool_calls=list(message.get("tool_calls") or []),
            raw_message=message,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )

    def _aria_chat(self, messages: list[dict]) -> LLMResponse:
        """Use the ARIA server's /api/aria/chat endpoint instead of an
        external LLM provider. This is the same endpoint the downloaded
        client uses — it works regardless of DeepSeek's status."""
        # Extract the last user message
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        if not last_user:
            raise LLMError("no user message found in conversation")

        url = f"{self.config.base_url}/chat"
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload = {
            "message": last_user,
            "session_id": f"cli_{os.environ.get('USERNAME', 'user')}",
            "auto_tools": True,
        }

        try:
            resp = self._client.post(url, json=payload, headers=headers, timeout=120.0)
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach ARIA server {url}: {exc}") from exc

        if resp.status_code == 401:
            raise LLMError(
                "ARIA server returned 401 Unauthorized. "
                "Set ARIA_INTERNAL_TOKEN or run: python aria.py --setup"
            )
        if resp.status_code >= 400:
            raise LLMError(
                f"ARIA server {url} returned HTTP {resp.status_code}: "
                f"{resp.text[:500]}"
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise LLMError(f"ARIA server returned non-JSON: {resp.text[:500]}") from exc

        response_text = data.get("response") or data.get("answer") or json.dumps(data)
        return LLMResponse(
            content=response_text,
            tool_calls=[],
            raw_message={"role": "assistant", "content": response_text},
            input_tokens=0,
            output_tokens=len(response_text),
        )

    def _aria_chat_stream(self, messages: list[dict], on_delta=None) -> LLMResponse:
        """Streaming chat via the ARIA server's /api/aria/chat/stream endpoint."""
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        if not last_user:
            raise LLMError("no user message found in conversation")

        url = f"{self.config.base_url}/chat/stream"
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload = {
            "message": last_user,
            "session_id": f"cli_{os.environ.get('USERNAME', 'user')}",
            "auto_tools": True,
        }

        content_parts: list[str] = []
        try:
            with self._client.stream("POST", url, json=payload, headers=headers, timeout=120.0) as resp:
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", errors="replace")
                    if resp.status_code == 401:
                        raise LLMError(
                            "ARIA server returned 401 Unauthorized. "
                            "Set ARIA_INTERNAL_TOKEN or run: python aria.py --setup"
                        )
                    raise LLMError(f"ARIA server {url} returned HTTP {resp.status_code}: {body[:500]}")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            parsed = json.loads(data_str)
                            text = parsed.get("text", parsed.get("response", ""))
                            if text:
                                content_parts.append(text)
                                if on_delta is not None:
                                    try:
                                        on_delta(text)
                                    except Exception:
                                        pass
                        except json.JSONDecodeError:
                            if data_str.strip():
                                content_parts.append(data_str)
                                if on_delta is not None:
                                    try:
                                        on_delta(data_str)
                                    except Exception:
                                        pass
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach ARIA server {url}: {exc}") from exc

        content = "".join(content_parts)
        return LLMResponse(
            content=content,
            tool_calls=[],
            raw_message={"role": "assistant", "content": content},
            input_tokens=0,
            output_tokens=len(content),
        )

    def chat_stream(self, messages: list[dict], tools: list[dict] | None = None,
                    on_delta=None) -> LLMResponse:
        """Streaming chat (SSE). Calls on_delta(text) for each content token as it
        arrives so the UI is never silent, accumulates tool_call deltas, and
        returns the same LLMResponse shape as chat(). Falls back semantics match
        chat() on errors (raises LLMError)."""
        # ARIA provider uses the server's /api/aria/chat/stream endpoint
        if self.config.provider == "aria":
            return self._aria_chat_stream(messages, on_delta)

        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload: dict = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        url = f"{self.config.base_url}/chat/completions"
        content_parts: list[str] = []
        tool_acc: dict[int, dict] = {}
        in_tok = out_tok = 0

        try:
            with self._client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", errors="replace")
                    raise LLMError(f"LLM endpoint {url} returned HTTP {resp.status_code}: {body[:500]}")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        chunk = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    usage = chunk.get("usage")
                    if usage:
                        in_tok = int(usage.get("prompt_tokens", 0) or 0) or in_tok
                        out_tok = int(usage.get("completion_tokens", 0) or 0) or out_tok
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        content_parts.append(piece)
                        if on_delta is not None:
                            try:
                                on_delta(piece)
                            except Exception:  # noqa: BLE001 — UI must never break the stream
                                pass
                    for tc in (delta.get("tool_calls") or []):
                        idx = tc.get("index", 0)
                        slot = tool_acc.setdefault(
                            idx, {"id": "", "type": "function",
                                  "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach LLM endpoint {url}: {exc}") from exc

        content = "".join(content_parts)
        tool_calls = [tool_acc[i] for i in sorted(tool_acc)]
        raw_message: dict = {"role": "assistant", "content": content}
        if tool_calls:
            raw_message["tool_calls"] = tool_calls
        self.total_input_tokens += in_tok
        self.total_output_tokens += out_tok
        return LLMResponse(
            content=content, tool_calls=tool_calls, raw_message=raw_message,
            input_tokens=in_tok, output_tokens=out_tok,
        )
