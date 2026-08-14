"""Capability tests for the 2026-07-25 LLM-chain outage (R-F3032..R-F3036).

Live symptom (verified from inside aria-intel, 2026-07-25 08:4x UTC):

    DEEPSEEK_API_KEY -> HTTP 400
    "The supported API model names are deepseek-v4-pro or deepseek-v4-flash,
     but you passed deepseek-chat."

`deepseek-chat` was retired. It is the PRIMARY provider's model, it is the
hardcoded model on the fallback chain's DeepSeek entry, and it is the
`_OPENAI_COMPAT_SAFE_DEFAULT` the R-F2935 guard degrades to — so every
non-DD LLM call 400'd with no working fallback behind it. Measured blast
radius: 258/258 indexed calls `model="fallback", success=False`, $0.00
spend for the day, while the autonomous engine kept firing 35 times an hour
into a dead chain.

Each test below drives the path that actually broke and asserts the
user-visible outcome, per CLAUDE.md §3c.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from aria_service.llm.openai_compat import OpenAICompatProvider
from aria_service.llm.provider import LLMProvider, LLMResult, ProviderError
from aria_service.llm import fallback as fb
from aria_service.llm import openai_compat as oc   # R-F3982 (C-70)


# Model ids DeepSeek retired. Anything still calling these gets HTTP 400.
RETIRED_DEEPSEEK_MODELS = {"deepseek-chat", "deepseek-reasoner"}


def _run(coro):
    return asyncio.run(coro)


class _StubProvider(LLMProvider):
    """Minimal provider double: records calls, succeeds or raises on demand."""

    def __init__(self, name: str, *, fails: bool = False, kind: str = "rate_limit"):
        self.name = name
        self._fails = fails
        self._kind = kind
        self.calls: list[dict] = []

    @property
    def is_configured(self) -> bool:
        return True

    async def complete(self, system_prompt, user_message, *, max_tokens=4096,
                       timeout=60.0, model=None) -> LLMResult:
        self.calls.append({"model": model, "max_tokens": max_tokens})
        if self._fails:
            raise ProviderError(self.name, "stub failure", kind=self._kind,
                                retryable=(self._kind != "auth"))
        return LLMResult(text="ok", model=model or self.name)


# ---------------------------------------------------------------------------
# R-F3032 — the retired model id must be gone from every default
# ---------------------------------------------------------------------------

def test_rf3032_openai_compat_safe_default_is_not_a_retired_model():
    """The R-F2935 'known-safe default' must not itself be a dead model.

    FAILS BEFORE: _OPENAI_COMPAT_SAFE_DEFAULT["deepseek"] == "deepseek-chat",
    so the guard that exists to rescue a bad secret degraded straight into
    the HTTP 400.
    """
    from aria_service.llm.openai_compat import _OPENAI_COMPAT_SAFE_DEFAULT

    assert _OPENAI_COMPAT_SAFE_DEFAULT["deepseek"] not in RETIRED_DEEPSEEK_MODELS, (
        "the deepseek safe-default is a RETIRED model id — the R-F2935 guard "
        "degrades a misconfigured provider into a guaranteed HTTP 400"
    )


def _chain_providers(chain) -> list:
    """create_fallback_chain returns a BARE provider (not a FallbackProvider)
    when only one provider is configured — which is itself the R-F3035 finding.
    Normalise so the assertions below read the same either way."""
    return list(getattr(chain, "providers", None) or [chain])


def test_rf3032_deepseek_chain_entry_model_is_env_driven_and_current(monkeypatch):
    """The fallback chain's DeepSeek entry must be env-driven, not hardcoded.

    FAILS BEFORE: fallback.py hardcoded ("deepseek", key, "deepseek-chat").
    Changing the LLM_MODEL secret could not reach it, so the documented fix
    for the outage ('set the secret') would have left this entry dead.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("ARIA_DEEPSEEK_CHAT_MODEL", "deepseek-v4-pro")
    for var in ("GROQ_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OLLAMA_URL"):
        monkeypatch.delenv(var, raising=False)

    chain = fb.create_fallback_chain(primary_provider="", primary_key="")
    ds = [p for p in _chain_providers(chain) if p.name == "deepseek"]
    assert ds, "deepseek should be in the chain when its key is set"

    models = {getattr(p, "_model", "") for p in ds}
    assert not (models & RETIRED_DEEPSEEK_MODELS), (
        f"chain still builds DeepSeek on a retired model: {models}"
    )
    assert "deepseek-v4-pro" in models, (
        f"ARIA_DEEPSEEK_CHAT_MODEL was ignored — entry is hardcoded. models={models}"
    )


# ---------------------------------------------------------------------------
# R-F3033 — a reasoning model's empty `content` must never read as success
# ---------------------------------------------------------------------------

def _fake_post(payload_json: dict, status: int = 200):
    """Patch httpx.AsyncClient.post with a canned chat-completions response."""
    class _Resp:
        status_code = status
        text = "stub"

        def json(self):
            return payload_json

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    return _Client


def test_rf3033_reasoning_content_is_used_when_content_is_empty(monkeypatch):
    """deepseek-v4-* put the answer in `reasoning_content` and leave
    `content` empty when the token budget is tight.

    Verified live 2026-07-25: deepseek-v4-flash at max_tokens=16 returned
    HTTP 200 with content:"" and a populated reasoning_content; at
    max_tokens=600 content was "OK.".

    FAILS BEFORE: complete() read only `content`, so a 200 with empty
    content returned LLMResult(text="") — a SILENT false success. That is
    worse than the 400 it replaced: the caller cannot tell "the model said
    nothing" from "the model was never asked".
    """
    import aria_service.llm.openai_compat as oc

    monkeypatch.setattr(oc.httpx, "AsyncClient", _fake_post({
        "choices": [{"message": {"content": "", "reasoning_content": "The answer is 42."}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "model": "deepseek-v4-flash",
    }))

    p = OpenAICompatProvider(name="deepseek", api_key="k", model="deepseek-v4-flash",
                             base_url="https://api.deepseek.com")
    res = _run(p.complete("sys", "usr", max_tokens=64))
    assert res.text.strip(), "empty content with populated reasoning_content returned an EMPTY result"
    assert "42" in res.text


def test_rf3033_all_empty_response_raises_instead_of_silent_success(monkeypatch):
    """A 200 with no usable text anywhere must RAISE, not return "".

    FAILS BEFORE: returned LLMResult(text="") and the fallback chain
    recorded a SUCCESS, so the chain stopped and never tried another
    provider for a call that produced nothing.
    """
    import aria_service.llm.openai_compat as oc

    monkeypatch.setattr(oc.httpx, "AsyncClient", _fake_post({
        "choices": [{"message": {"content": "", "reasoning_content": ""}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0},
        "model": "deepseek-v4-flash",
    }))

    p = OpenAICompatProvider(name="deepseek", api_key="k", model="deepseek-v4-flash",
                             base_url="https://api.deepseek.com")
    with pytest.raises(ProviderError):
        _run(p.complete("sys", "usr", max_tokens=64))


# ---------------------------------------------------------------------------
# R-F3034 — a DD pinned to Claude must NEVER be served by DeepSeek
# ---------------------------------------------------------------------------

def test_rf3034_preferred_claude_does_not_degrade_to_deepseek():
    """Operator directive 2026-07-25: 'DD reports are to be ran fully on
    Claude no deepseek, and deepseek is for everything else.'

    FAILS BEFORE: complete() built order = [preferred] + ordinary providers,
    so a rate-limited Claude silently handed DD synthesis to DeepSeek. The
    DD line's whole guarantee is never-false-clean; a DeepSeek-authored
    verdict on a Claude-pinned run is a fabrication risk, and the DD already
    has an honest failure path (INSUFFICIENT EVIDENCE / AMBER-LIGHT), so
    failing is strictly better than degrading.
    """
    anthropic = _StubProvider("anthropic", fails=True, kind="rate_limit")
    deepseek = _StubProvider("deepseek", fails=False)
    chain = fb.FallbackProvider([anthropic, deepseek])

    with pytest.raises(ProviderError):
        _run(chain.complete("sys", "usr", prefer_provider="anthropic"))

    assert deepseek.calls == [], (
        "a Claude-pinned (DD) call was served by DeepSeek — operator directive "
        "violated and the DD verdict would carry DeepSeek-generated content"
    )


def test_rf3034_unpinned_call_still_uses_deepseek():
    """The other half of the directive: everything else runs on DeepSeek.

    Guards against over-correcting R-F3034 into 'nothing ever falls back'.
    """
    deepseek = _StubProvider("deepseek", fails=False)
    chain = fb.FallbackProvider([deepseek])
    res = _run(chain.complete("sys", "usr"))
    assert res.text == "ok"
    assert len(deepseek.calls) == 1


# ---------------------------------------------------------------------------
# R-F3035 - a single model retirement must not zero the non-DD chain
#
# -- R-F3982 (C-70) - RE-EXPRESSED, NOT DELETED, after R-F3943 ---------------
#
# The two tests that lived here asserted `len(default_order) >= 2`: the chain
# must always hold a SECOND DeepSeek entry on a different model id. R-F3943 then
# removed that entry BY OPERATOR DIRECTIVE ("just remove deepseek back up, we do
# not need a backup"), so both went red - red because the POLICY changed, not
# because the code broke. That is the R-F3859 shape: a red test can be the
# defect, and the obvious way to green one is to delete the offending line.
#
# Deleting them would have thrown away four assertions that survive R-F3943
# untouched, and one real production bug they were built to catch:
#
#   * no RETIRED model id may reach the chain (the original R-F3032 outage)
#   * provider NAMES must be unique, or FallbackProvider per-name cooldowns
#     collide and the second entry is never tried
#   * `_stats` must cover every provider
#   * built the PRODUCTION way (primary_provider="deepseek"), the
#     `name == primary_provider` skip must not drop the fallback entries -
#     the exact bug that made the original test pass while production was broken
#
# So the guarantee is split into what it always actually was:
#
#   test_rf3035_backup_is_OFF_by_default - the POLICY R-F3943 set, pinned so it
#       cannot silently revert and resume billing ~3x/token.
#   test_rf3035_the_backup_MECHANISM_still_works - R-F3035 machinery, proven
#       intact under an explicit opt-in. The capability was disabled, not
#       deleted; if it is ever re-enabled it must still work.
#   test_rf3035_what_protects_us_now - what replaced the second entry: an
#       env-driven model id (a retirement is a secret change, not a deploy) plus
#       R-F3036 dead-chain loudness, tested below and green.
# ---------------------------------------------------------------------------

def test_rf3035_backup_is_OFF_by_default(monkeypatch):
    """R-F3943 policy: the second DeepSeek slot is DISABLED unless asked for.

    Not a preference - a measured one. Both slots are built from the SAME
    `DEEPSEEK_API_KEY` on the same account, so it was never redundancy against
    an account/key/network failure, and the backup model cost ~3x the primary
    per token ($0.572/M vs $0.193/M, measured 2026-08-12) across 1,584 calls
    nobody asked it to serve.

    FAILS IF: the backup silently returns, i.e. paid traffic resumes with no
    operator decision.
    """
    monkeypatch.delenv("ARIA_DEEPSEEK_BACKUP_ENABLED", raising=False)
    assert oc.deepseek_backup_enabled() is False
    assert oc.backup_deepseek_model() == "", (
        "a disabled backup must resolve to NO model id - returning one lets a "
        "caller re-add the slot by accident"
    )

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    for var in ("GROQ_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OLLAMA_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("ARIA_DEEPSEEK_CHAT_MODEL", raising=False)

    chain = fb.create_fallback_chain(primary_provider="deepseek", primary_key="sk-test")
    pref_only = fb.preference_only_providers()
    default_order = [p for p in _chain_providers(chain)
                     if (p.name or "").lower() not in pref_only]
    assert len(default_order) == 1, (
        f"the default chain should hold exactly the primary after R-F3943, got "
        f"{[(p.name, getattr(p, '_model', '')) for p in default_order]}"
    )
    # The surviving R-F3032 guarantee: whatever IS in the chain is not retired.
    models = {getattr(p, "_model", "") for p in default_order}
    assert not (models & RETIRED_DEEPSEEK_MODELS), f"retired model in chain: {models}"


def test_rf3035_a_typo_cannot_re_enable_paid_traffic(monkeypatch):
    """Only explicit truthy words count. The safe default is the one that does
    NOT spend, so a mistyped value must fail CLOSED."""
    for raw in ("", "0", "false", "no", "off", "ture", "Y", "enabled", " "):
        monkeypatch.setenv("ARIA_DEEPSEEK_BACKUP_ENABLED", raw)
        assert oc.deepseek_backup_enabled() is False, f"{raw!r} enabled paid traffic"
    for raw in ("1", "true", "TRUE", "yes", "on", " on "):
        monkeypatch.setenv("ARIA_DEEPSEEK_BACKUP_ENABLED", raw)
        assert oc.deepseek_backup_enabled() is True, f"{raw!r} did not enable it"


def test_rf3035_the_backup_MECHANISM_still_works(monkeypatch):
    """R-F3035 machinery, proven intact under an explicit opt-in.

    The capability was DISABLED, not deleted. If an operator ever re-enables it,
    every property the original test guarded must still hold - otherwise
    "re-enable the backup" would silently produce a chain that cannot fail over.

    Built the PRODUCTION way (primary_provider="deepseek"), because that is the
    branch where the original R-F3035 test passed while production was broken:
    the loop `name == primary_provider` skip dropped BOTH DeepSeek fallback
    entries, including the new backup.
    """
    monkeypatch.setenv("ARIA_DEEPSEEK_BACKUP_ENABLED", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ARIA_ANTHROPIC_ENABLED", "1")
    for var in ("GROQ_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OLLAMA_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("ARIA_DEEPSEEK_CHAT_MODEL", raising=False)
    monkeypatch.delenv("ARIA_DEEPSEEK_BACKUP_MODEL", raising=False)

    chain = fb.create_fallback_chain(primary_provider="deepseek", primary_key="sk-test")
    provs = _chain_providers(chain)
    pref_only = fb.preference_only_providers()
    default_order = [p for p in provs if (p.name or "").lower() not in pref_only]

    assert len(default_order) >= 2, (
        f"opted IN and the production shape still drops the backup: "
        f"{[(p.name, getattr(p, '_model', '')) for p in default_order]}"
    )
    models = {getattr(p, "_model", "") for p in default_order}
    assert len(models) >= 2, f"both default members run the same model: {models}"
    assert not (models & RETIRED_DEEPSEEK_MODELS), f"retired model in chain: {models}"

    names = [p.name for p in provs]
    assert len(set(names)) == len(names), (
        f"duplicate provider names {names} - FallbackProvider keys per-provider "
        f"cooldowns by name, so these would share one and the backup would "
        f"never be reached"
    )
    stats = getattr(chain, "_stats", {})
    if stats:
        assert set(stats) == set(names), (
            f"stats keys {sorted(stats)} do not cover every provider {names}"
        )


def test_rf3035_what_protects_us_now_that_the_chain_is_one_deep(monkeypatch):
    """R-F3035 guarded MODEL RETIREMENT. With the backup off, two things do.

    (1) The model id is env-driven, so a retirement is a SECRET CHANGE rather
        than a code deploy - what made the 2026-07-25 outage total was the id
        being hardcoded in eight places.
    (2) A dead chain is LOUD (R-F3036), tested immediately below and green, so
        a retirement surfaces in minutes instead of reading as $0.00 spend.

    Pinned so nobody re-adds a paid warm spare believing it is the only
    protection available.
    """
    monkeypatch.setenv("ARIA_DEEPSEEK_CHAT_MODEL", "deepseek-v9-hypothetical")
    assert oc.default_deepseek_model() == "deepseek-v9-hypothetical", (
        "the primary model id is not env-driven - recovering from a retirement "
        "would need a code deploy, which is the R-F3032 outage"
    )
    monkeypatch.delenv("ARIA_DEEPSEEK_CHAT_MODEL", raising=False)
    assert oc.default_deepseek_model() not in RETIRED_DEEPSEEK_MODELS, (
        "the built-in default is a RETIRED id - the rescue path degrades into "
        "the outage it exists to prevent"
    )
    assert any(n.startswith("test_rf3036") for n in globals()), (
        "R-F3036 dead-chain loudness tests are gone - with the backup removed "
        "they are the remaining protection against a silent model retirement"
    )


# ---------------------------------------------------------------------------
# R-F3036 — a fully dead chain must reach the brain and the operator
# ---------------------------------------------------------------------------

def test_rf3036_provider_http_failure_reaches_the_brain(monkeypatch):
    """§25a proprioception: ARIA must KNOW when her own LLM limb is dead.

    MEASURED 2026-07-25: 258/258 LLM calls failed over ~2h40m, and NOT ONE
    of the 106 modules on /api/aria/brain/stats recorded a single failure
    (`fail=0` everywhere). Cost records booked each failure as
    model="fallback", cost_usd=0.0, so the daily spend line read $0.00 —
    indistinguishable from a quiet day. The limb was dead and invisible.

    ROOT CAUSE (corrected — the signal is not missing, it is EXEMPTED):
    openai_compat.complete is decorated
        @fail_wire(..., control_flow_exempt=("ProviderError",))
    and its only explicit wire_failure calls sit on httpx.TimeoutException
    and httpx.HTTPError. An HTTP 4xx/5xx is raised as ProviderError from
    `raise ProviderError.from_http_status(...)` and re-raised by a bare
    `except ProviderError: raise` — so the single most common provider
    failure is the one path that emits nothing.

    Exempting ProviderError from the GAP is right (the chain may still
    recover via fallback, so it is control flow, not a capability gap).
    Emitting no HEALTH signal is not: provider health has to be observable
    whether or not the chain recovered.

    FAILS BEFORE: an HTTP 400 produces zero brain signals.
    """
    import aria_service.intel.engine_wiring as ew
    import aria_service.llm.openai_compat as oc

    seen: list[dict] = []
    monkeypatch.setattr(ew, "wire_failure",
                        lambda **kw: seen.append(kw), raising=False)
    monkeypatch.setattr(oc.httpx, "AsyncClient", _fake_post({}, status=400))

    p = OpenAICompatProvider(name="deepseek", api_key="k", model="deepseek-v4-flash",
                             base_url="https://api.deepseek.com")
    with pytest.raises(ProviderError):
        _run(p.complete("sys", "usr", max_tokens=64))

    assert seen, (
        "an HTTP 400 from the provider emitted NO brain signal — this is the "
        "exact blind spot that let 258/258 failures run unnoticed"
    )
    joined = " ".join(str(s) for s in seen).lower()
    assert "deepseek" in joined, f"signal does not name the failing provider: {seen}"


def test_rf3036_chain_exhaustion_is_observable(monkeypatch):
    """When EVERY provider is down, that must be its own signal.

    A per-provider failure is routine (the chain may recover). A fully
    exhausted chain means ARIA has no LLM at all — the condition that ran
    unnoticed for hours — and must be distinguishable from a single
    provider blip.
    """
    import aria_service.intel.engine_wiring as ew

    seen: list[dict] = []
    monkeypatch.setattr(ew, "wire_failure",
                        lambda **kw: seen.append(kw), raising=False)

    a = _StubProvider("deepseek", fails=True, kind="other")
    b = _StubProvider("groq", fails=True, kind="other")
    chain = fb.FallbackProvider([a, b])

    with pytest.raises(ProviderError):
        _run(chain.complete("sys", "usr"))

    joined = " ".join(str(s) for s in seen).lower()
    assert seen and ("all" in joined or "exhaust" in joined), (
        f"chain exhaustion is not distinguishable from a single provider "
        f"failure in the brain signal: {seen}"
    )
