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
import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("aria_cli.llm")

# Base-url defaults mirror aria_service/llm/factory.py so the CLI talks to the
# same endpoints ARIA already uses.
#: R-F4370 (C-315) — DEEPSEEK IS DELIBERATELY ABSENT FROM EVERY TABLE BELOW.
#: Operator directive 2026-08-26: "remove deepseek from cli, aria must use her
#: own reasoning now". Removing it from the tables — rather than only changing
#: the default — is the whole point: while `deepseek` remained a resolvable
#: name it could still be reached by `--provider`, by LLM_PROVIDER, or by the
#: unknown-provider base-url fallback that used to send ANY unrecognised
#: provider to api.deepseek.com. A vendor that can still be selected by a typo
#: has not been removed.
_PROVIDER_BASE_URLS = {
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


#: Context window per provider, in tokens. R-F4318 (C-266) — the CLI used to
#: assume a large window everywhere: max_tokens defaulted to 8192 and the
#: compaction budget to 180,000 chars, both fine for deepseek-chat (~64K) and
#: both fatal against the 16,384-token sovereign, which answered
#: "requested 71611 tokens (63419 in the messages, 8192 in the completion)".
#: A window is a property of the MODEL, so it lives here rather than in a
#: constant that silently encodes one vendor's capacity.
_PROVIDER_WINDOWS = {
    "openai":     128000,
    "groq":       131072,
    "openrouter": 128000,
    "mistral":    32768,
    "ollama":     32768,
    "aria":       32768,
    "aria-llm":   16384,   # overridden by ARIA_LLM_MAX_MODEL_LEN
}
_DEFAULT_WINDOW = 65536

#: R-F4370 (C-315) — the CLI's default provider. ARIA reasons for herself.
_DEFAULT_PROVIDER = "aria-llm"

#: Providers deliberately withdrawn, and why. Kept as a NAMED REFUSAL rather
#: than deleted silently: a bare KeyError would send the next reader to
#: re-add the table entry, which is the revert this repo has already watched
#: happen three times to a Brave/WA capability (CLAUDE.md §17).
_REMOVED_PROVIDERS = {
    "deepseek": (
        "provider 'deepseek' has been removed from the ARIA coder CLI "
        "(R-F4370, operator directive 2026-08-26: \"remove deepseek from cli, "
        "aria must use her own reasoning now\"). The coder runs on ARIA's own "
        "model: set ARIA_LLM_URL + ARIA_LLM_MODEL and leave "
        "ARIA_CODER_LLM_PROVIDER unset (or =aria-llm). Do not re-add a vendor "
        "entry to fix this message."
    ),
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
    #: R-F4318 — the served model's context window. MUST match the server's
    #: --max-model-len; a value larger than the server's produces the exact
    #: HTTP 400 this fixes.
    max_model_len: int = _DEFAULT_WINDOW
    temperature: float = 0.0

    @classmethod
    def from_env(cls, provider_override: str = "") -> "LLMConfig":
        """Resolve config from env. ARIA_CODER_* overrides, then the generic
        LLM_*/DEEPSEEK_* vars that the rest of the stack already uses.

        R-F4370 (C-315) — THE DEFAULT IS THE SOVEREIGN, AND DEEPSEEK IS GONE.
        Operator directive 2026-08-26: "remove deepseek from cli, aria must use
        her own reasoning now."

        This reverses R-F1937, which defaulted to DIRECT ``deepseek`` whenever
        DEEPSEEK_API_KEY was present. That reasoning is still sound about the
        thing it measured — the ``aria`` provider hits the brain's heavy
        full-chat pipeline and times out (~122s), so a coder must never default
        THERE — but ``aria`` and ``aria-llm`` are different endpoints. The
        sovereign vLLM answers in ~2-8s with native tool-calling, so the
        premise that forced a vendor default no longer holds.

        It fails CLOSED. With no ``ARIA_LLM_URL`` this raises rather than
        selecting some other model, for the same reason R-F4303 refuses to
        guess a version: being answered by a model you did not choose, with
        nothing in the output saying so, is worse than being told no.
        """
        # R-F4303 (C-256) — a --provider flag must reach RESOLUTION, not be
        # stamped on afterwards. cli.py used to call from_env() and only then set
        # cfg.provider, which renamed the provider while leaving base_url pointing
        # at whatever from_env had already chosen. Asking for the sovereign model
        # and being answered by DeepSeek, with nothing in the output saying so, is
        # the failure that makes a comparison meaningless.
        # R-F4318 (C-266) — the override is APPLIED, never EXPORTED. The first
        # version of this wrote provider_override into os.environ, which made
        # reading the config mutate the process: every later caller in the same
        # process inherited the override, and it leaked across test boundaries
        # (it broke a peer's sub-agent test that passes in isolation). A getter
        # with a side effect on global state is a defect regardless of whether
        # the value is right.
        provider = (
            (provider_override or "").strip()
            or os.getenv("ARIA_CODER_LLM_PROVIDER")
            or os.getenv("LLM_PROVIDER")
            or _DEFAULT_PROVIDER
        ).strip().lower()

        # R-F4370 (C-315) — a REMOVED provider must say so, not fail obscurely.
        # Without this, `--provider deepseek` resolves an empty base_url and the
        # CLI dies on a malformed URL, which reads as a bug rather than as the
        # directive it is. Naming the directive is what stops a future session
        # "restoring" the table entry to fix the error message.
        if provider in _REMOVED_PROVIDERS:
            raise LLMError(_REMOVED_PROVIDERS[provider], transient=False)

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
            # R-F4370 (C-315) — no vendor default. This used to end in
            # `.get(provider, "https://api.deepseek.com/v1")`, so ANY
            # unrecognised provider — a typo, a removed name, a new one nobody
            # wired — was silently answered by DeepSeek. That is the same
            # class as R-F4303's refusal to guess a version: the output looks
            # identical whichever model served it.
            base_url = (
                os.getenv("ARIA_CODER_LLM_BASE_URL")
                or os.getenv("OPENAI_BASE_URL")
                or (f"{ollama_url}/v1" if provider == "ollama"
                    else _PROVIDER_BASE_URLS.get(provider, ""))
            ).rstrip("/")
            if not base_url:
                raise LLMError(
                    f"provider {provider!r} has no endpoint. Set "
                    f"ARIA_CODER_LLM_BASE_URL, or use ARIA's own model "
                    f"(unset ARIA_CODER_LLM_PROVIDER, with ARIA_LLM_URL set). "
                    f"Refusing to fall back to another vendor.",
                    transient=False)

        timeout = float(os.getenv("ARIA_CODER_LLM_TIMEOUT", "30"))
        max_tokens = int(os.getenv("ARIA_CODER_LLM_MAX_TOKENS", "8192"))

        # R-F4318 (C-266) — the window is the MODEL's, so read it from the model.
        # ARIA_LLM_MAX_MODEL_LEN must match the vLLM --max-model-len; everything
        # else falls back to a per-provider default rather than one global
        # constant that assumes the largest vendor.
        try:
            _win = int((os.getenv("ARIA_LLM_MAX_MODEL_LEN") or "").strip())
        except (TypeError, ValueError):
            _win = 0
        if _win < 512:
            _win = _PROVIDER_WINDOWS.get(provider, _DEFAULT_WINDOW)

        # Reserve at most a QUARTER of the window for the answer. 8192 against a
        # 16,384 window is half the context gone before a single message exists,
        # which is how an empty conversation still overflowed.
        _cap = max(256, _win // 4)
        if max_tokens > _cap:
            max_tokens = _cap
        return cls(
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            max_tokens=max_tokens,
            max_model_len=_win,
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


#: Mistral's chat template enforces two rules that vLLM rejects with HTTP 400,
#: and BOTH are invisible until a real tool loop runs. Measured live against the
#: sovereign endpoint 2026-08-25:
#:
#:   consecutive same-role messages
#:     -> "After the optional system message, conversation roles must alternate
#:         user/assistant/user/assistant/..."
#:   any tool-call id that is not exactly 9 alphanumeric chars
#:     -> "Tool call IDs should be alphanumeric strings with length 9!"
#:
#: The second is the one that bites hardest: our ids look like "call_abc12345"
#: (13) or "c1" (2), so EVERY tool round-trip 400s - the CLI could call a tool
#: once and never feed the result back. She emits valid ids herself; it is the
#: ids we echo back that are rejected.
_MISTRAL_TOOL_ID_LEN = 9


def _mistral_tool_id(raw: str) -> str:
    """A stable 9-char alphanumeric id derived from ``raw``.

    Deterministic so the id on an assistant ``tool_calls`` entry and the one on
    the matching ``tool`` message map to the SAME value - rewriting them
    independently would break the pairing and trade one 400 for another.
    """
    import hashlib

    if len(raw) == _MISTRAL_TOOL_ID_LEN and raw.isalnum():
        return raw
    return hashlib.md5(raw.encode("utf-8", "replace")).hexdigest()[:_MISTRAL_TOOL_ID_LEN]


def _mistral_contract(messages: list[dict]) -> list[dict]:
    """Return a COPY of ``messages`` satisfying Mistral's chat-template contract.

    Applied only for the sovereign provider (see `chat`). Two repairs:

      1. consecutive same-role turns are MERGED rather than dropped. Dropping
         one would silently discard something the user or the model actually
         said; merging preserves every token and only loses the turn boundary.
      2. tool-call ids are rewritten to 9 alphanumeric chars, consistently
         across the assistant entry and its matching tool result.

    Pure: does not mutate the input.
    """
    # -- 1. ids ------------------------------------------------------------
    fixed: list[dict] = []
    for m in messages:
        m = dict(m)
        calls = m.get("tool_calls")
        if isinstance(calls, list) and calls:
            new_calls = []
            for c in calls:
                c = dict(c)
                if c.get("id"):
                    c["id"] = _mistral_tool_id(str(c["id"]))
                new_calls.append(c)
            m["tool_calls"] = new_calls
        if m.get("tool_call_id"):
            m["tool_call_id"] = _mistral_tool_id(str(m["tool_call_id"]))
        fixed.append(m)

    # -- 2. hoist stray system turns --------------------------------------
    # Mistral allows ONE system message and only in the leading position
    # ("After the optional system message, ..."). The agent appends a second one
    # MID-CONVERSATION: `agent.py` injects the code-RAG block as a system turn
    # after the user's task, so the very first real CLI run 400'd even with the
    # alternation merge below in place — every unit test passed and the live
    # path still failed, which is why §23 requires the operator's actual path.
    #
    # Hoisting rather than dropping or re-roling: the block is instruction, it
    # stays instruction, and every character survives.
    sys_parts = [(m.get("content") or "") for m in fixed if m.get("role") == "system"]
    if len(sys_parts) > 1 or (sys_parts and fixed and fixed[0].get("role") != "system"):
        rest = [m for m in fixed if m.get("role") != "system"]
        merged = "\n\n".join(p for p in sys_parts if p).strip()
        fixed = ([{"role": "system", "content": merged}] if merged else []) + rest

    # -- 3. alternation ----------------------------------------------------
    out: list[dict] = []
    for m in fixed:
        role = m.get("role")
        if role in ("user", "assistant") and out and out[-1].get("role") == role:
            prev = out[-1]
            a, b = prev.get("content") or "", m.get("content") or ""
            prev["content"] = (a + "\n\n" + b).strip() if (a and b) else (a or b)
            # A single assistant turn may legitimately request several tools;
            # concatenating keeps every call rather than losing the later ones.
            pc, mc = prev.get("tool_calls") or [], m.get("tool_calls") or []
            if pc or mc:
                prev["tool_calls"] = list(pc) + list(mc)
        else:
            out.append(m)
    return out


#: Providers observed to emit tool calls into the content channel. Scoped, so a
#: provider that uses the proper channel is never second-guessed — parsing its
#: content could only ever create false positives.
_CONTENT_TOOLCALL_PROVIDERS = frozenset({"aria-llm"})


def _extract_call_blocks(body: str) -> tuple[list, str]:
    """Pull LINE-ANCHORED JSON tool-call blocks out of ``body``.

    R-F4329 (C-277). The measured live output is not one clean array — it is
    arrays SEPARATED BY PROSE, because she narrates between the steps she
    plans:

        [{"name": "run", ...}]
        After the test run, if it still fails, inspect the code...
        [{"name": "edit_file", ...}]
        After editing the file, run the tests again.

    A first version required the whole content to PARSE as JSON, which
    correctly refused this and therefore recovered nothing on the case that
    matters. The discriminator that separates it from the dangerous case is
    POSITION, not punctuation: a block she is EMITTING starts a line, while a
    block she is TALKING ABOUT ("here is what I would send: [...] — shall
    I?") sits inside a sentence. Only line-anchored blocks are considered,
    and each must still parse whole and validate element-by-element upstream.

    Returns ``(items, leftover_prose)``; ``([], body)`` when nothing qualifies.
    """
    nl = chr(10)
    items: list = []
    prose: list[str] = []
    lines = body.split(nl)
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if not (stripped.startswith("[{") or stripped.startswith("{\"name\"")
                or stripped.startswith("[ {")):
            prose.append(line)
            i += 1
            continue
        # Greedily take the shortest run of lines from here that parses whole.
        taken = None
        for j in range(i, min(i + 40, len(lines))):
            chunk = nl.join(lines[i:j + 1]).strip()
            try:
                parsed = json.loads(chunk)
            except Exception:  # noqa: BLE001 — keep extending
                continue
            taken = (parsed, j)
            break
        if taken is None:
            prose.append(line)
            i += 1
            continue
        parsed, j = taken
        items.extend(parsed if isinstance(parsed, list) else [parsed])
        i = j + 1
    return items, nl.join(prose).strip()


def _derive_tool_name(args: dict, tools: list[dict]) -> str:
    """The single offered tool whose schema accepts exactly these keys, or "".

    R-F4368 (C-314). Measured 2026-08-26: the sovereign's second-turn
    ``[TOOL_CALLS]`` array sometimes carries ``arguments`` with NO ``name`` at
    all — ``[{"arguments": {"command": "python hello.py"}}]``.

    This is a DERIVATION, not a guess, and the difference is the uniqueness
    check: the keys must satisfy one offered tool's required set, fall inside
    its declared properties, and match NO OTHER offered tool. Two candidates
    means there is nothing to derive and we refuse — picking one would be
    invention, and a fabricated ``run`` EXECUTES.
    """
    if not isinstance(args, dict) or not args:
        return ""
    keys = set(args)
    hits: list[str] = []
    for t in tools or []:
        fn = (t or {}).get("function") or {}
        name = fn.get("name")
        params = fn.get("parameters") or {}
        props = set((params.get("properties") or {}).keys())
        required = set(params.get("required") or [])
        if not name or not props:
            continue
        # Every supplied key must be declared, and every required key supplied.
        if keys <= props and required <= keys:
            hits.append(name)
    return hits[0] if len(hits) == 1 else ""


def _pair_split_tool_call_items(items: list, tools: list[dict]) -> list:
    """Normalise the sovereign's malformed second-turn arrays to ``items``.

    R-F4368 (C-314). Two shapes, both measured live, both refused by vLLM's
    Mistral parser (correctly — ``raw["name"]`` raises), so the call was lost::

        [{"arguments": {...}}, {"name": "edit_file", "id": "104be20cf"}]
        [{"arguments": {"command": "python hello.py"}}]

    In the first the name is PRESENT, one object over; pairing it is bookkeeping,
    not interpretation. In the second it is derived, or refused.

    Returns ``items`` UNCHANGED when it is already well-formed or cannot be
    repaired — the caller's R-F4329 validation then rejects it, so a failure to
    repair can never become a permissive path.
    """
    if not isinstance(items, list) or not items:
        return items
    if not all(isinstance(it, dict) for it in items):
        return items
    # Already canonical (or something else entirely) — do not touch it.
    if all(it.get("name") and isinstance(it.get("arguments"), dict) for it in items):
        return items

    arg_objs = [it for it in items if isinstance(it.get("arguments"), dict)
                and not it.get("name")]
    name_objs = [it for it in items if it.get("name")
                 and not isinstance(it.get("arguments"), dict)]
    if not arg_objs:
        return items

    # The split shape: pair positionally, and only when the counts line up.
    # A mismatch means we would be choosing which name goes with which call.
    if name_objs:
        if len(name_objs) != len(arg_objs):
            return items
        return [{"name": n.get("name"), "arguments": a.get("arguments")}
                for a, n in zip(arg_objs, name_objs)]

    # The nameless shape: derive each, and refuse the WHOLE array if any one
    # cannot be derived — R-F4329's all-or-nothing rule, for the same reason.
    paired = []
    for a in arg_objs:
        name = _derive_tool_name(a.get("arguments"), tools)
        if not name:
            return items
        paired.append({"name": name, "arguments": a.get("arguments")})
    return paired if len(paired) == len(items) else items


def recover_tool_calls_from_content(
    content: str, tools: list[dict] | None, provider: str,
) -> tuple[list[dict], str]:
    """Recover tool calls the model wrote into ``content`` instead of emitting.

    R-F4329 (C-277). Measured live: when the sovereign plans ONE call she uses
    the tool_calls channel; when she plans SEVERAL she writes a Mistral-shaped
    array as text —

        [{"name": "run", "arguments": {...}},
         {"name": "edit_file", "arguments": {...}}]

    — deterministically, and multi-step plans are exactly what a coding task
    produces. That is why every real coding request ended "files: 0 changed,
    tools: 0 calls" while single-step questions worked.

    This is a CHANNEL failure, not a comprehension failure: she named real
    tools with real arguments. Recovering it is a transport-layer repair of a
    known provider quirk, the same class as `_mistral_contract` (R-F4320) and
    `_sanitize_messages` (R-F1290).

    IT MUST NOT BECOME A PROSE PARSER. Inventing a call the model did not make
    is worse than dropping one, because a fabricated ``run`` EXECUTES. Every
    condition here is therefore load-bearing:

      * tools must have been OFFERED — with none, any match is pure invention;
      * the content must PARSE as JSON (after stripping Mistral's [TOOL_CALLS]
        token or a ```json fence), not merely CONTAIN it, so prose that quotes
        a call is left alone;
      * every element must be an object whose ``name`` is an OFFERED tool and
        whose ``arguments`` is an object;
      * ONE bad element rejects the WHOLE array — partial recovery would run
        half a plan the model never intended as a half.

    Returns ``(calls, remaining_content)``. On no recovery the content comes
    back byte-identical.
    """
    if (provider or "").strip().lower() not in _CONTENT_TOOLCALL_PROVIDERS:
        return [], content
    if not tools:
        return [], content
    text = (content or "").strip()
    if not text:
        return [], content

    offered = set()
    for t in tools:
        fn = (t or {}).get("function") or {}
        if fn.get("name"):
            offered.add(fn["name"])
    if not offered:
        return [], content

    body = text
    if body.startswith("[TOOL_CALLS]"):
        body = body[len("[TOOL_CALLS]"):].strip()
    if body.startswith("```"):
        _nl = chr(10)
        body = body.split(_nl, 1)[-1] if _nl in body else body
        if body.endswith("```"):
            body = body[:-3]
        body = body.strip()

    items, leftover = _extract_call_blocks(body)
    if not items:
        return [], content

    # R-F4368 (C-314) — repair the two malformed arrays the sovereign emits on
    # a SECOND turn before validating. Leaves a well-formed array untouched, so
    # every guarantee below is still enforced on whatever comes out.
    items = _pair_split_tool_call_items(items, tools)

    calls: list[dict] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            return [], content
        name = it.get("name")
        args = it.get("arguments")
        if name not in offered or not isinstance(args, dict):
            return [], content
        calls.append({
            "id": _mistral_tool_id(f"rec{i}{name}"),
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        })

    return calls, leftover


def _repair_streamed_arguments(fragments: list[str]) -> tuple[str, bool]:
    """R-F4351 (C-296) — assemble streamed ``arguments`` deltas into valid JSON.

    The OpenAI streaming contract says argument deltas concatenate, and for a
    well-formed stream that is exactly right. vLLM's Mistral tool parser does
    something else. MEASURED LIVE 2026-08-26 against the sovereign pod: the
    SAME payload non-streamed returns a clean call, while streamed it sends 21
    deltas whose first 20 assemble to a string missing the closing quote —

        delta[11] 'l'   delta[17] ', "timeout": 1'

    — and then delta 21 re-emits the COMPLETE object::

        '{"command": "cat README.md | wc -l", "timeout": 10}'

    Concatenating all 21 yields ``{"command": "cat README.md | wc -l,
    "timeout": 10{"command": ...}``, which fails ``json.loads``. So the
    transport CORRUPTED a call the model got right, and the turn ended
    "tools: 0 calls" with the model narrating about JSON formatting.

    Two conditions are load-bearing, because a repair that could invent a call
    would be worse than the defect — a fabricated ``run`` EXECUTES:

      * the concatenation is tried FIRST and returned untouched when it parses,
        so a healthy stream is never second-guessed;
      * only a fragment that is ITSELF a complete JSON **object** may be taken,
        and the LAST such wins (the re-emission is the model's final word).
        A fragment like ``'0'`` or ``'l'`` parses as JSON but is not an
        arguments object, and taking it would silently drop every real field.

    When nothing complete was ever emitted — the genuinely truncated case,
    where vLLM stopped at an invalid ``\\C`` escape and the rest of the
    generation was discarded — the broken text is handed back unchanged so
    ``agent.py`` reports an honest parse error. There is nothing to recover and
    guessing a command is not an option.

    Returns ``(arguments, repaired)``. Pure: does not mutate ``fragments``.
    """
    joined = "".join(fragments)
    try:
        json.loads(joined)
        return joined, False          # healthy stream — leave it alone
    except Exception:  # noqa: BLE001 — malformed: look for a re-emission
        pass
    for frag in reversed(fragments):
        candidate = frag.strip()
        try:
            parsed = json.loads(candidate)
        except Exception:  # noqa: BLE001 — not a complete value; keep looking
            continue
        # The dict test is the whole guard, not a formality: a lone ``0`` or
        # ``10`` delta is VALID JSON, and taking one would replace the whole
        # arguments object with a scalar — silently dropping every real field.
        if isinstance(parsed, dict):
            return candidate, True
    return joined, False              # nothing complete — stay honest


def _arguments_parse(tool_call: dict) -> bool:
    """True when this tool call's ``arguments`` are usable by ``agent.py``.

    R-F4367 (C-313). ``agent.py:944`` does ``json.loads(raw_args)`` and reports
    "could not parse arguments as JSON" on failure, so this asks exactly the
    question that decides whether the tool will RUN — not a looser one. A dict
    is already parsed and counts as fine; anything else is checked as text.
    """
    args = (tool_call.get("function") or {}).get("arguments")
    if isinstance(args, dict):
        return True
    if not isinstance(args, str):
        return False
    try:
        json.loads(args)
    except Exception:  # noqa: BLE001 — unparseable is the whole question
        return False
    return True


def _wire_messages(messages: list[dict], provider: str) -> list[dict]:
    """The ONE thing that prepares a message array for the wire.

    Both the blocking and the streaming path call this, deliberately: §13's
    stream-bypass rule exists because a guard added to one path and not the
    other is the repeat failure in this repo, and a tool loop that 400s only
    when streaming would be near-impossible to attribute.

    The Mistral repair is scoped to the sovereign provider because it IS the
    sovereign endpoint's constraint; DeepSeek accepts the unrepaired array and
    merging its turns would lose information for no reason.
    """
    msgs = _sanitize_messages(messages)          # R-F1290 tool-call contract
    if (provider or "").strip().lower() == "aria-llm":
        msgs = _mistral_contract(msgs)           # R-F4320 Mistral template
    return msgs


def _repair_tool_call_arguments(msg: dict) -> dict:
    """R-F4343 (C-287) — force every ``arguments`` field to be valid JSON.

    MEASURED LIVE on a real coding turn: the sovereign emitted a tool call whose
    arguments were cut off mid-value —

        {"command": "pytest C:\\\\Users\\\\anton\\\\...\\\\calc.py

    — no closing quote, no closing brace. `agent.py` HANDLES that correctly: it
    catches the parse error and records a tool result saying so. But the broken
    assistant message stayed in the history and was echoed on the NEXT request,
    and the provider rejects it:

        HTTP 400 "Unterminated string starting at: line 1 column 13 (char 12)"

    So one malformed generation wedged every subsequent turn of the session —
    the operator's "files: 0 changed, tools: 0 calls" on real coding work. The
    model's own mistake is survivable; echoing it back is what made it fatal.

    Replacing the text with ``{}`` loses nothing: the tool result already told
    the model its arguments were unparseable, so the feedback survives while the
    payload becomes valid. Keeping the call (rather than dropping it) also keeps
    its tool result from becoming an orphan, which the pass below would then
    discard — taking the error message with it.

    NOT scoped to a provider: an ``arguments`` value that is not a JSON string is
    invalid in the OpenAI tool contract for everyone, so this can only ever
    repair something already broken. A dict is serialised rather than blanked —
    some providers send one, and that IS recoverable.
    """
    calls = msg.get("tool_calls") or []
    repaired: list[dict] = []
    changed = False
    for call in calls:
        fn = call.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                json.loads(args)
                repaired.append(call)
                continue
            except Exception:  # noqa: BLE001 — malformed: fall through to repair
                replacement = "{}"
        elif isinstance(args, dict):
            replacement = json.dumps(args)
        else:
            replacement = "{}"
        changed = True
        repaired.append({**call, "function": {**fn, "arguments": replacement}})
    return {**msg, "tool_calls": repaired} if changed else msg


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """R-F1290 — transport-layer last line of defense. Return a COPY of
    ``messages`` that satisfies the provider's tool-call contract, so a corrupted
    history can never 400 the API. Three failure modes are repaired:

      * an ORPHAN ``tool`` message (not preceded by an assistant ``tool_calls``
        block) → dropped. Otherwise: HTTP 400 "Messages with role 'tool' must be
        a response to a preceding message with 'tool_calls'".
      * a ``tool_calls`` with no following tool message → a synthetic error
        response is inserted. Otherwise: HTTP 400 "tool_calls must be followed by
        tool messages".
      * an ``arguments`` string that is not valid JSON → replaced with ``{}``
        (R-F4343). Otherwise: HTTP 400 "Unterminated string ...", which wedged
        every remaining turn of the session.

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
        if role == "assistant" and m.get("tool_calls"):
            m = _repair_tool_call_arguments(m)  # R-F4343 (C-287)
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
        #: R-F4351 (C-296) — §21a metrics. Repairs mean the serving stack is
        #: emitting partial deltas; failures mean a call was genuinely lost.
        self.stream_arg_repairs = 0
        self.stream_arg_failures = 0
        #: R-F4367 (C-313) — corrupt streamed turns rescued by a non-streamed
        #: re-issue. A rising count means the pod's tool parser is degrading;
        #: a silent repair would hide that.
        self.stream_arg_reissues = 0

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

    def _reissue_unstreamed(self, messages: list[dict],
                            tools: list[dict] | None) -> "LLMResponse | None":
        """R-F4367 (C-313) — re-ask the SAME question without streaming.

        Returns the clean response, or ``None`` to keep the streamed one. It is
        a repair attempt, never a new dependency: every failure path returns
        ``None`` so the caller hands ``agent.py`` the honest broken call rather
        than killing the session.

        Three refusals are load-bearing, because inventing a call is worse than
        losing one — a fabricated ``run`` EXECUTES:

          * the re-issue raised            → keep the original;
          * it produced no tool call       → keep the original, because an empty
            answer is not evidence the model wanted nothing;
          * its arguments are ALSO corrupt → keep the original, so the parse
            error the operator sees names the model's first attempt.
        """
        try:
            resp = self.chat(messages, tools=tools)
        except Exception as exc:  # noqa: BLE001 — a repair must not raise
            logger.warning("[R-F4367] non-streamed re-issue failed (%s); "
                           "passing the corrupt streamed call through", exc)
            return None
        if not resp.tool_calls:
            logger.warning("[R-F4367] non-streamed re-issue returned no tool "
                           "call; keeping the corrupt streamed one")
            return None
        if not all(_arguments_parse(tc) for tc in resp.tool_calls):
            logger.warning("[R-F4367] non-streamed re-issue is ALSO corrupt; "
                           "staying honest rather than guessing a call")
            return None
        self.stream_arg_reissues += 1
        logger.warning(
            "[R-F4367] streamed tool arguments were truncated by the serving "
            "stack; recovered %d call(s) by re-issuing non-streamed",
            len(resp.tool_calls))
        return resp

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        # ARIA provider uses the server's /api/aria/chat endpoint
        if self.config.provider == "aria":
            return self._aria_chat(messages)

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload: dict = {
            "model": self.config.model,
            "messages": _wire_messages(messages, self.config.provider),
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

        _content = message.get("content") or ""
        _calls = list(message.get("tool_calls") or [])
        if not _calls:
            # R-F4329 (C-277) — she plans multi-step work as content JSON.
            _rec, _content2 = recover_tool_calls_from_content(
                _content, tools, self.config.provider)
            if _rec:
                _calls, _content = _rec, _content2
                message = {**message, "tool_calls": _calls, "content": _content}
                logger.info("[R-F4329] recovered %d tool call(s) the model "
                            "emitted as content", len(_calls))
        return LLMResponse(
            content=_content,
            tool_calls=_calls,
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
            "messages": _wire_messages(messages, self.config.provider),
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
        arg_frags: dict[int, list[str]] = {}   # R-F4351 — per-slot argument deltas
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
                            # R-F4351: keep the FRAGMENTS, not just their
                            # concatenation — the re-emission repair below can
                            # only see vLLM's final complete object if the
                            # delta boundaries survive.
                            arg_frags.setdefault(idx, []).append(fn["arguments"])
        except httpx.HTTPError as exc:
            # R-F1418 — network/DNS/timeout errors are always transient
            raise LLMError(f"could not reach LLM endpoint {url}: {exc}", transient=True) from exc

        content = "".join(content_parts)
        # R-F4351 (C-296) — resolve each slot's argument deltas. §21a: BOTH
        # outcomes are announced, because a silent repair hides a serving
        # defect and a silent failure is the "tools: 0 calls" turn this fixes.
        for idx, frags in arg_frags.items():
            resolved, repaired = _repair_streamed_arguments(frags)
            tool_acc[idx]["function"]["arguments"] = resolved
            name = tool_acc[idx]["function"].get("name") or "?"
            if repaired:
                self.stream_arg_repairs += 1
                logger.warning(
                    "[R-F4351] streamed arguments for '%s' did not parse across "
                    "%d deltas; recovered the re-emitted complete object "
                    "(vLLM partial-delta tool parser)", name, len(frags))
            else:
                try:
                    json.loads(resolved)
                except Exception:  # noqa: BLE001 — honest failure, not a repair
                    self.stream_arg_failures += 1
                    logger.warning(
                        "[R-F4351] streamed arguments for '%s' are unparseable "
                        "and no complete object was emitted across %d deltas; "
                        "passing through for an honest parse error: %.200s",
                        name, len(frags), resolved)
        tool_calls = [tool_acc[i] for i in sorted(tool_acc)]
        if not tool_calls:
            # R-F4329 (C-277) — §13: the stream-bypass rule exists because a
            # guard on one transport and not the other is this repo's repeat
            # failure, and a coding turn that only works unstreamed would be
            # near-impossible to attribute.
            _rec, _rest = recover_tool_calls_from_content(
                content, tools, self.config.provider)
            if _rec:
                tool_calls, content = _rec, _rest
                logger.info("[R-F4329] recovered %d tool call(s) from streamed "
                            "content", len(_rec))
        self.total_input_tokens += in_tok
        self.total_output_tokens += out_tok

        # R-F4367 (C-313) — the stream truncated a call the model got right.
        # Measured 2026-08-26 on the sovereign pod: the same five payloads gave
        # 5/5 clean tool calls non-streamed and 2/5 streamed, and all three
        # streamed failures were `run` — i.e. exactly "she cannot run any
        # command". A dropped closing quote cannot be reconstructed without
        # guessing, so we do not guess: we re-ask the SAME question over the
        # channel that is not broken. Stronger evidence, not a better guess.
        if tool_calls and any(not _arguments_parse(tc) for tc in tool_calls):
            reissued = self._reissue_unstreamed(messages, tools)
            if reissued is not None:
                return reissued

        raw_message: dict = {"role": "assistant", "content": content}
        if tool_calls:
            raw_message["tool_calls"] = tool_calls
        return LLMResponse(
            content=content, tool_calls=tool_calls, raw_message=raw_message,
            input_tokens=in_tok, output_tokens=out_tok,
        )
