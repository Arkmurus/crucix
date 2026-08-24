"""R-F988 — LLM client for the ARIA Coder CLI.

A thin, synchronous OpenAI-compatible chat client with native tool/function
calling. DeepSeek is the default backend (the only active provider per
CLAUDE.md §18), but any OpenAI-compatible endpoint works (openai, groq,
openrouter, mistral, ollama) by setting the provider/base-url env vars.

We do NOT reuse ``aria_service.llm.OpenAICompatProvider`` here because that
abstraction is plain text-in/text-out (no ``tools`` array, no ``tool_calls``
parsing) — an agent loop needs function calling. The env-var names and base-url
defaults are kept identical to ``aria_service.llm.factory`` for consistency.

Stronger coding model (R-F2165): the client is provider-pluggable. To route the
coder to Claude (Sonnet/Opus class) for higher coding quality while keeping
native tool-calling, set THREE env vars and flip — no code change:
    ARIA_CODER_LLM_PROVIDER=openrouter
    OPENROUTER_API_KEY=<key>
    ARIA_CODER_LLM_MODEL=<the exact OpenRouter Claude slug, e.g. anthropic/claude-sonnet-4.5>
OpenRouter exposes Claude over an OpenAI-shaped API, so it works through this
same client (sidesteps the missing native Anthropic adapter + the declined
direct-Anthropic billing per CLAUDE.md §18). Until flipped, the default stays
``deepseek-chat`` (operator decision 2026-06-30: keep DeepSeek for now).

NOTE — the in-house ``aria`` provider does NOT support tool-calling (it forwards
the last user message to the brain's /chat and returns text). A coder on the
``aria`` provider has NO tools and degrades to a chat box; ``supports_tools`` is
False for it so callers can warn loudly instead of failing silently.
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
    # R-F4303 (C-256) — the SOVEREIGN model, not the service. `aria` targets
    # {ARIA_SERVICE_URL}/api/aria (the chat API: ~122s per R-F1937, tool-less per
    # R-F2166). This one targets ARIA_LLM_URL, the OpenAI-compatible vLLM
    # endpoint serving ARIA_LLM_MODEL. Set dynamically; never defaulted, because
    # a default here would silently answer from a different model.
    "aria-llm": "",
}
_PROVIDER_DEFAULT_MODELS = {
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "openrouter/auto",
    "mistral": "mistral-large-latest",
    "ollama": "llama3.1:8b",
    "aria": "aria-coder",
    # R-F4303 — deliberately EMPTY. The served id must come from ARIA_LLM_MODEL:
    # naming a default here would invent a version, which is exactly how live sat
    # on `aria-llm-v0.1` for months while only v0.2 and v0.4 had been evaluated.
    "aria-llm": "",
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
    # R-F4303 — its OWN key var. ARIA_INTERNAL_TOKEN authenticates the SERVICE;
    # sending it to the model endpoint is the same class of mistake R-F1280
    # exists to prevent. vLLM often serves unauthenticated, so empty is valid.
    "aria-llm": "ARIA_LLM_KEY",
}


class LLMError(RuntimeError):
    """Raised when the LLM endpoint is unreachable or returns an error.

    Attributes:
        transient: True if the error is likely transient (network blip, DNS,
                   timeout, 5xx) and worth retrying. False for hard errors
                   (auth, bad request, context length).
    """
    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


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
    def from_env(cls, provider_override: str = "") -> "LLMConfig":
        """Resolve config from env. ARIA_CODER_* overrides, then the generic
        LLM_*/DEEPSEEK_* vars that the rest of the stack already uses.

        R-F1937: default to DIRECT ``deepseek`` when DEEPSEEK_API_KEY is present.
        The old default ``aria`` builds base_url ``{ARIA_SERVICE_URL}/api/aria``
        and chat() appends ``/chat/completions`` — i.e. it hits the HEAVY brain
        full-chat pipeline, NOT a fast coder endpoint (the prior docstring's
        "/api/aria/coder/llm" claim was wrong). That path times out (~122s
        measured), so a *coder* CLI must never default to it. Direct deepseek
        answers the same prompt in ~1.7s. ``aria`` stays available as an explicit
        opt-in via ARIA_CODER_LLM_PROVIDER/LLM_PROVIDER=aria; if no deepseek key
        is set we fall back to ``aria`` (unchanged from before).
        """
        # R-F4303 (C-256) — a --provider flag must reach RESOLUTION, not be
        # stamped on afterwards. cli.py used to call from_env() and only then set
        # cfg.provider, which renamed the provider while leaving base_url pointing
        # at whatever from_env had already chosen. Asking for the sovereign model
        # and being answered by DeepSeek, with nothing in the output saying so, is
        # the failure that makes a comparison meaningless.
        if (provider_override or "").strip():
            os.environ["ARIA_CODER_LLM_PROVIDER"] = provider_override.strip()
        provider = (
            os.getenv("ARIA_CODER_LLM_PROVIDER")
            or os.getenv("LLM_PROVIDER")
            or ("deepseek" if os.getenv("DEEPSEEK_API_KEY") else "aria")
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

        if provider == "aria-llm":
            # R-F4303 (C-256) — the sovereign vLLM endpoint.
            #
            # FAIL CLOSED AND LOUDLY when unconfigured. Falling back to DeepSeek
            # or localhost would answer from a DIFFERENT model than the operator
            # selected, which makes any comparison or eval meaningless and is
            # invisible in the output.
            raw = (os.getenv("ARIA_LLM_URL") or "").strip().rstrip("/")
            if not raw:
                raise LLMError(
                    "provider 'aria-llm' needs ARIA_LLM_URL (the sovereign vLLM "
                    "endpoint). Refusing to fall back to another model.",
                    transient=False,
                )
            base_url = raw if raw.endswith("/v1") else raw + "/v1"
            # The served id lives in ARIA_LLM_MODEL - the same var aria-intel
            # reads - so the CLI and the service can never address different
            # models. An explicit ARIA_CODER_LLM_MODEL / LLM_MODEL still wins,
            # because that is the operator deliberately overriding.
            if not model:
                model = (os.getenv("ARIA_LLM_MODEL") or "").strip()
            if not model:
                raise LLMError(
                    "provider 'aria-llm' needs ARIA_LLM_MODEL (e.g. "
                    "aria-llm-v0.4-dpo). Refusing to guess a version.",
                    transient=False,
                )
            if not api_key:
                api_key = os.getenv("ARIA_LLM_KEY", "")
        elif provider == "aria":
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
        #
        # R-F4303 (C-256) — `aria-llm` joins it. It is our OWN vLLM endpoint and
        # is commonly served without auth; rejecting that with "No LLM API key
        # found" would send the operator hunting for a key that does not exist.
        # Deliberately narrow: every remote vendor still requires one.
        return self.provider in ("ollama", "aria-llm") or bool(self.api_key)


@dataclass
class LLMResponse:
    """One assistant turn. ``tool_calls`` is the OpenAI-shaped list (each item
    has ``id`` and ``function.name`` / ``function.arguments``)."""
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    raw_message: dict = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """R-F1290 — transport-layer last line of defense. Return a COPY of
    ``messages`` that satisfies the provider's tool-call contract, so a corrupted
    history can never 400 the API. Two failure modes are repaired:

      * an ORPHAN ``tool`` message (not preceded by an assistant ``tool_calls``
        block) → dropped. Otherwise: HTTP 400 "Messages with role 'tool' must be
        a response to a preceding message with 'tool_calls'".
      * a ``tool_calls`` with no following tool message → a synthetic error
        response is inserted. Otherwise: HTTP 400 "tool_calls must be followed by
        tool messages".

    The agent loop (agent.py) already repairs its own history (R-F1120/R-F1283),
    but a zombie timeout thread, a resumed session, or any future caller could
    still hand us a bad array — this guarantees the wire payload is always valid.
    Pure: does not mutate the input.
    """
    out: list[dict] = []
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        role = m.get("role")
        if role == "tool":
            i += 1  # orphan — drop
            continue
        out.append(m)
        i += 1
        if role == "assistant" and m.get("tool_calls"):
            have = set()
            while i < n and messages[i].get("role") == "tool":
                out.append(messages[i])
                have.add(messages[i].get("tool_call_id"))
                i += 1
            for tc in (m.get("tool_calls") or []):
                tcid = tc.get("id")
                if tcid and tcid not in have:
                    out.append({
                        "role": "tool",
                        "tool_call_id": tcid,
                        "content": "error: tool call did not complete; no result available.",
                    })
    return out


class LLMClient:
    """Synchronous OpenAI-compatible chat-completions client with tools."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.from_env()
        self._client = httpx.Client(timeout=self.config.timeout)
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    @property
    def supports_tools(self) -> bool:
        """R-F2166: False on the in-house ``aria`` provider, which forwards text
        to the brain /chat and cannot do function-calling — so a coder on it has
        NO tools. Callers warn loudly instead of silently degrading to a chat box.

        R-F4303 (C-256): ``aria-llm`` is OPT-IN. vLLM serves function-calling only
        when launched with a tool-call parser, so tool support is a property of
        how the pod was started, not of the provider. Guessing True would make
        tool calls fail silently mid-task; guessing False forever would cap the
        CLI at a chat box. Default False (the caller already warns loudly), and
        ``ARIA_LLM_SUPPORTS_TOOLS=1`` turns it on once the pod actually serves
        them."""
        if self.config.provider == "aria-llm":
            return (os.getenv("ARIA_LLM_SUPPORTS_TOOLS") or "").strip().lower() in (
                "1", "true", "yes", "on")
        return self.config.provider != "aria"

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
            "messages": _sanitize_messages(messages),  # R-F1290
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
            # R-F1418 — network/DNS/timeout errors are always transient
            raise LLMError(f"could not reach LLM endpoint {url}: {exc}", transient=True) from exc

        if resp.status_code >= 400:
            # R-F1418 — 429/5xx are transient; 4xx (except 429) are hard errors
            _is_transient_http = resp.status_code in (429,) or resp.status_code >= 500
            raise LLMError(
                f"LLM endpoint {url} returned HTTP {resp.status_code}: "
                f"{resp.text[:500]}",
                transient=_is_transient_http,
            )

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"LLM endpoint returned non-JSON: {resp.text[:500]}", transient=False) from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"LLM endpoint returned no choices: {json.dumps(data)[:500]}", transient=False)

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
            raise LLMError("no user message found in conversation", transient=False)

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
            # R-F1418 — network/DNS/timeout errors are always transient
            raise LLMError(f"could not reach ARIA server {url}: {exc}", transient=True) from exc

        if resp.status_code == 401:
            raise LLMError(
                "ARIA server returned 401 Unauthorized. "
                "Set ARIA_INTERNAL_TOKEN or run: python aria.py --setup",
                transient=False,
            )
        if resp.status_code >= 400:
            # R-F1418 — 429/5xx are transient; other 4xx are hard errors
            _is_transient_http = resp.status_code in (429,) or resp.status_code >= 500
            raise LLMError(
                f"ARIA server {url} returned HTTP {resp.status_code}: "
                f"{resp.text[:500]}",
                transient=_is_transient_http,
            )

        try:
            data = resp.json()
        except Exception as exc:
            raise LLMError(f"ARIA server returned non-JSON: {resp.text[:500]}", transient=False) from exc

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
            raise LLMError("no user message found in conversation", transient=False)

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
                            "Set ARIA_INTERNAL_TOKEN or run: python aria.py --setup",
                            transient=False,
                        )
                    # R-F1418 — 429/5xx are transient; other 4xx are hard errors
                    _is_transient_http = resp.status_code in (429,) or resp.status_code >= 500
                    raise LLMError(
                        f"ARIA server {url} returned HTTP {resp.status_code}: {body[:500]}",
                        transient=_is_transient_http,
                    )
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
            # R-F1418 — network/DNS/timeout errors are always transient
            raise LLMError(f"could not reach ARIA server {url}: {exc}", transient=True) from exc

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
            "messages": _sanitize_messages(messages),  # R-F1290
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
                    # R-F1418 — 429/5xx are transient; other 4xx are hard errors
                    _is_transient_http = resp.status_code in (429,) or resp.status_code >= 500
                    raise LLMError(
                        f"LLM endpoint {url} returned HTTP {resp.status_code}: {body[:500]}",
                        transient=_is_transient_http,
                    )
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
            # R-F1418 — network/DNS/timeout errors are always transient
            raise LLMError(f"could not reach LLM endpoint {url}: {exc}", transient=True) from exc

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
