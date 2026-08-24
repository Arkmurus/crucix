"""R-F4303 / C-256 - the CLI had no way to reach the sovereign model.

The operator asked for "the aria cli terminal to use the aria llm reasoning".
The natural reading is `--provider aria`. That is the WRONG path and two
R-numbers record why:

  * `aria` targets `{ARIA_SERVICE_URL}/api/aria` - the SERVICE chat API, not the
    model. R-F1937 measured it answering in ~122s where direct DeepSeek took
    ~1.7s, and demoted it from the default for exactly that reason.
  * R-F2166 records that it cannot do function-calling at all, so a coder agent
    on it silently degrades to a chat box.

The sovereign model is a different thing entirely: an OpenAI-compatible vLLM
endpoint at `ARIA_LLM_URL`, serving `ARIA_LLM_MODEL` (now `aria-llm-v0.4-dpo`,
the best-measured adapter at 0.502 on the frozen 500-Q against DeepSeek's 0.336).
Nothing in the CLI could address it. This adds `aria-llm` as its own provider.

THE TWO PROVIDERS MUST NOT BE CONFLATED, and the tests below pin that: they
resolve different base URLs from different env vars with different credentials.
Collapsing them would send ARIA's INTERNAL TOKEN to the model endpoint, which is
the precise mistake R-F1280 exists to prevent ("never ARIA's internal token to an
external API - that 401'd every DeepSeek call").

TOOL SUPPORT IS OPT-IN, NOT ASSUMED. vLLM serves function-calling only when
launched with a tool-call parser, and the pod is down so it cannot be probed.
Guessing True would make tool calls fail silently mid-task; guessing False
forever would cap the CLI at a chat box. So it defaults False - the CLI already
warns loudly on that (R-F2166) rather than degrading quietly - and is enabled by
`ARIA_LLM_SUPPORTS_TOOLS=1` once the pod is actually configured for it.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_cli import llm as cli_llm  # noqa: E402

_ENV = (
    "ARIA_LLM_URL", "ARIA_LLM_MODEL", "ARIA_LLM_KEY", "ARIA_LLM_SUPPORTS_TOOLS",
    "ARIA_SERVICE_URL", "ARIA_INTERNAL_TOKEN",
    "ARIA_CODER_LLM_PROVIDER", "LLM_PROVIDER", "ARIA_CODER_LLM_MODEL",
    "LLM_MODEL", "ARIA_CODER_LLM_BASE_URL", "OPENAI_BASE_URL",
    "DEEPSEEK_API_KEY", "LLM_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in _ENV:
        monkeypatch.delenv(v, raising=False)


def _cfg(monkeypatch, **env):
    # A model id is REQUIRED by design (fail-closed, see the no-model test), so
    # supply one unless the case under test overrides it. Tests that exercise the
    # refusal set the env directly rather than going through here.
    env.setdefault("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "aria-llm")
    return cli_llm.LLMConfig.from_env()


# -- it is addressable at all -----------------------------------------------

def test_the_provider_is_registered() -> None:
    assert "aria-llm" in cli_llm._PROVIDER_KEY_ENV, "aria-llm is not a known provider"


def test_it_resolves_the_sovereign_endpoint(monkeypatch) -> None:
    """THE CAPABILITY TEST - the CLI must reach the MODEL, not the service."""
    c = _cfg(monkeypatch,
             ARIA_LLM_URL="https://pod-8888.proxy.runpod.net/v1",
             ARIA_LLM_MODEL="aria-llm-v0.4-dpo")
    assert c.provider == "aria-llm"
    assert c.base_url == "https://pod-8888.proxy.runpod.net/v1"
    assert c.model == "aria-llm-v0.4-dpo"


def test_it_does_NOT_resolve_the_service_chat_api(monkeypatch) -> None:
    """The bug this prevents: silently landing on the 122s tool-less path."""
    c = _cfg(monkeypatch,
             ARIA_LLM_URL="https://pod-8888.proxy.runpod.net/v1",
             ARIA_SERVICE_URL="https://aria-intel.fly.dev")
    assert "/api/aria" not in c.base_url, (
        "aria-llm resolved to the SERVICE chat API - that is the `aria` provider, "
        "which R-F1937 measured at ~122s and R-F2166 records as tool-less")


def test_a_url_without_v1_still_works(monkeypatch) -> None:
    c = _cfg(monkeypatch, ARIA_LLM_URL="https://pod-8888.proxy.runpod.net")
    assert c.base_url.endswith("/v1")


def test_v1_is_never_doubled(monkeypatch) -> None:
    c = _cfg(monkeypatch, ARIA_LLM_URL="https://pod-8888.proxy.runpod.net/v1/")
    assert c.base_url.count("/v1") == 1, c.base_url


# -- credentials: never the wrong key ---------------------------------------

def test_it_never_sends_the_internal_token(monkeypatch) -> None:
    """R-F1280's lesson. ARIA_INTERNAL_TOKEN authenticates the SERVICE; sending it
    to the model endpoint is the same class of mistake that 401'd every DeepSeek
    call. The sovereign endpoint has its own key var."""
    c = _cfg(monkeypatch,
             ARIA_LLM_URL="https://pod-8888.proxy.runpod.net/v1",
             ARIA_INTERNAL_TOKEN="internal-secret-value")
    assert c.api_key != "internal-secret-value"


def test_it_uses_its_own_key_var(monkeypatch) -> None:
    c = _cfg(monkeypatch,
             ARIA_LLM_URL="https://pod-8888.proxy.runpod.net/v1",
             ARIA_LLM_KEY="vllm-key")
    assert c.api_key == "vllm-key"


def test_an_empty_key_is_allowed(monkeypatch) -> None:
    """vLLM commonly serves without auth; an empty key must not be an error."""
    c = _cfg(monkeypatch, ARIA_LLM_URL="https://pod-8888.proxy.runpod.net/v1")
    assert c.api_key == ""


# -- fail CLOSED and VISIBLY when unconfigured ------------------------------

def test_no_url_refuses_rather_than_falling_back(monkeypatch) -> None:
    """Falling back to DeepSeek or localhost would silently answer from a
    different model than the operator asked for - the failure mode that makes an
    eval meaningless."""
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "aria-llm")
    with pytest.raises(cli_llm.LLMError) as e:
        cli_llm.LLMConfig.from_env()
    assert "ARIA_LLM_URL" in str(e.value)


def test_no_model_refuses_rather_than_guessing(monkeypatch) -> None:
    """An empty model id 400s on every call. Naming a default here would invent a
    version - live pointed at aria-llm-v0.1 for months, which nothing evaluated."""
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "aria-llm")
    monkeypatch.setenv("ARIA_LLM_URL", "https://pod-8888.proxy.runpod.net/v1")
    with pytest.raises(cli_llm.LLMError) as e:
        cli_llm.LLMConfig.from_env()
    assert "ARIA_LLM_MODEL" in str(e.value)


# -- tool support is opt-in -------------------------------------------------

def test_tools_are_off_by_default(monkeypatch) -> None:
    c = _cfg(monkeypatch,
             ARIA_LLM_URL="https://pod-8888.proxy.runpod.net/v1",
             ARIA_LLM_MODEL="aria-llm-v0.4-dpo")
    client = cli_llm.LLMClient(c)
    try:
        assert client.supports_tools is False
    finally:
        client.close()


def test_tools_can_be_enabled_when_the_pod_serves_them(monkeypatch) -> None:
    c = _cfg(monkeypatch,
             ARIA_LLM_URL="https://pod-8888.proxy.runpod.net/v1",
             ARIA_LLM_MODEL="aria-llm-v0.4-dpo",
             ARIA_LLM_SUPPORTS_TOOLS="1")
    client = cli_llm.LLMClient(c)
    try:
        assert client.supports_tools is True
    finally:
        client.close()


def test_the_legacy_aria_provider_still_has_no_tools(monkeypatch) -> None:
    """R-F2166 must not be undone by adding a sibling provider."""
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "aria")
    monkeypatch.setenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev")
    client = cli_llm.LLMClient(cli_llm.LLMConfig.from_env())
    try:
        assert client.supports_tools is False
    finally:
        client.close()


def test_other_providers_keep_their_tools(monkeypatch) -> None:
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    client = cli_llm.LLMClient(cli_llm.LLMConfig.from_env())
    try:
        assert client.supports_tools is True
    finally:
        client.close()


# -- the CLI layer: the flag must actually re-resolve the endpoint ----------

def test_the_provider_override_re_resolves_the_endpoint(monkeypatch) -> None:
    """THE CLI-LAYER BUG.

    `cli.py` called `LLMConfig.from_env()` and only THEN applied
    `--provider`, so the flag renamed the provider while leaving `base_url`
    pointing at whatever from_env had already chosen — DeepSeek by default. The
    operator would ask for the sovereign model and be answered by DeepSeek, with
    nothing in the output saying so. The override has to reach resolution.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("ARIA_LLM_URL", "https://pod-8888.proxy.runpod.net/v1")
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")
    c = cli_llm.LLMConfig.from_env(provider_override="aria-llm")
    assert c.provider == "aria-llm"
    assert c.base_url == "https://pod-8888.proxy.runpod.net/v1", (
        "the --provider override did not re-resolve base_url — the CLI would "
        "answer from DeepSeek while claiming to be on the sovereign model")


def test_no_override_keeps_env_behaviour(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    c = cli_llm.LLMConfig.from_env()
    assert c.provider == "deepseek"


def test_a_keyless_sovereign_endpoint_is_configured(monkeypatch) -> None:
    """vLLM commonly serves with no auth. Rejecting that with 'No LLM API key
    found' would send the operator hunting for a key that does not exist."""
    c = _cfg(monkeypatch, ARIA_LLM_URL="https://pod-8888.proxy.runpod.net/v1")
    assert c.api_key == ""
    assert c.is_configured is True


def test_other_providers_still_require_a_key(monkeypatch) -> None:
    """The exemption must be narrow — it must not disable the check generally."""
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "deepseek")
    c = cli_llm.LLMConfig.from_env()
    assert c.is_configured is False
