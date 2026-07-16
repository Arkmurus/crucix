"""R-F2645 — kill the /v1 ambiguity class: all callers share ONE URL join.

R-F2641 fixed the symptom: `resilience.py` appended `/v1/chat/completions` to a
base that already carried `/v1`, requesting `/v1/v1/chat/completions` → 404
against a HEALTHY endpoint → `aria_llm` breaker tripped → sovereign reported
DOWN while UP (the promotion-gate corruption R-F2566 warns about).

But the CLASS survived: three call sites each re-invented the join and
disagreed about whether `ARIA_LLM_URL` carries `/v1`:

    aria_llm_provider.py:159/:276  f"{base}/chat/completions"   # /v1 present
    self_healing.py:232           base.rstrip("/") + "/models"  # /v1 present
    resilience.py:208             f"{base}/v1/chat/completions" # /v1 absent  <- shipped

Nothing stopped a fourth caller inventing a fifth opinion, so R-F2645 moved the
join into `llm/aria_llm_url.py`. The decisive test here is
`test_all_call_sites_agree_on_the_same_base` — it compares the URLs the three
REAL call sites build from ONE env value, which is exactly the assertion that
would have caught R-F2641 before it shipped.

Fail-closed is deliberate (see the module docstring): a missing `/v1` is NOT
auto-repaired, because repairing it only in the probe is what produces a
false-green — probe healthy, provider 404ing on every real call.
"""

from __future__ import annotations

from typing import Any

import pytest

from aria_service.llm import aria_llm_url

_BASE_WITH_V1 = "https://pod-8888.proxy.runpod.net/v1"


# ── the shared join ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (_BASE_WITH_V1, "https://pod-8888.proxy.runpod.net/v1"),
        (_BASE_WITH_V1 + "/", "https://pod-8888.proxy.runpod.net/v1"),
        (_BASE_WITH_V1 + "///", "https://pod-8888.proxy.runpod.net/v1"),
        ("  " + _BASE_WITH_V1 + "  ", "https://pod-8888.proxy.runpod.net/v1"),
        ("", ""),
    ],
)
def test_normalise_base(raw: str, expected: str) -> None:
    assert aria_llm_url.normalise_base(raw) == expected


def test_joins_never_double_v1() -> None:
    """The R-F2641 regression, pinned at the source of truth."""
    assert aria_llm_url.chat_completions_url(_BASE_WITH_V1) == (
        "https://pod-8888.proxy.runpod.net/v1/chat/completions"
    )
    assert aria_llm_url.models_url(_BASE_WITH_V1) == (
        "https://pod-8888.proxy.runpod.net/v1/models"
    )
    assert "/v1/v1/" not in aria_llm_url.chat_completions_url(_BASE_WITH_V1)
    assert "/v1/v1/" not in aria_llm_url.models_url(_BASE_WITH_V1)


def test_missing_v1_is_reported_not_repaired() -> None:
    """Fail closed: never silently rewrite a misconfigured endpoint.

    Auto-appending /v1 here would make the probe green while
    aria_llm_provider 404s on every real call — a false-green admitting a dead
    provider to the chain head.
    """
    bare = "https://pod-8888.proxy.runpod.net"

    assert aria_llm_url.looks_v1_shaped(_BASE_WITH_V1) is True
    assert aria_llm_url.looks_v1_shaped(bare) is False
    # Reported, not repaired — the join stays faithful to what was configured.
    assert aria_llm_url.chat_completions_url(bare) == f"{bare}/chat/completions"


# ── the class guard: the three real call sites must agree ───────────────────

@pytest.mark.asyncio
async def test_all_call_sites_agree_on_the_same_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE CLASS TEST: probe, provider and self_healing derive the same base.

    Drives the REAL call sites (not copies of the string) against one
    ARIA_LLM_URL and asserts none of them doubles or drops a /v1. Pre-R-F2641
    the probe URL alone was /v1/v1/... and this would have failed.
    """
    monkeypatch.setenv("ARIA_LLM_URL", _BASE_WITH_V1)

    captured: list[str] = []

    class _Resp:
        status_code = 200
        text = '{"choices":[{"message":{"content":"ok"}}]}'

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    class _FakeClient:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *_a: Any) -> bool:
            return False

        async def post(self, url: str, **_kw: Any) -> _Resp:
            captured.append(url)
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    # 1. the health probe (resilience.py) — the site that shipped broken
    from aria_service.llm import resilience

    checker = resilience.LLMHealthChecker(endpoint=_BASE_WITH_V1)
    await checker._probe()
    probe_url = captured[-1]

    # 2. the provider (aria_llm_provider.py) — the site users actually hit
    from aria_service.llm import aria_llm_provider

    await aria_llm_provider.complete("hi", max_tokens=5)
    provider_url = captured[-1]

    # 3. self_healing's subsystem check builds /models off the same base
    healing_url = aria_llm_url.models_url(_BASE_WITH_V1)

    assert probe_url == provider_url == (
        "https://pod-8888.proxy.runpod.net/v1/chat/completions"
    ), f"probe and provider disagree: probe={probe_url!r} provider={provider_url!r}"
    assert healing_url == "https://pod-8888.proxy.runpod.net/v1/models"

    for url in (probe_url, provider_url, healing_url):
        assert "/v1/v1/" not in url

    # The probe must be UP iff the provider is UP — same URL, same verdict.
    assert checker.probe_status == "healthy"


def test_no_call_site_rebuilds_the_join_by_hand() -> None:
    """Anti-drift: a fourth caller must not invent a fifth opinion.

    Greps the real modules for a hand-rolled '/v1/chat/completions' or
    '/chat/completions' f-string. This is what makes R-F2645 a class fix rather
    than three coincidentally-correct callers.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    offenders: list[str] = []

    # Both shapes the three sites actually used before R-F2645:
    #   f-string  : f"{base}/chat/completions"        (aria_llm_provider, resilience)
    #   concat    : base.rstrip("/") + "/models"      (self_healing)
    # A guard that caught only the f-string would miss self_healing entirely.
    _fstring_join = re.compile(r'f["\'].*\{.*\}/(v1/)?(chat/completions|models)')
    _concat_join = re.compile(r'\+\s*["\']/(v1/)?(chat/completions|models)["\']')

    # fallback.py is the 4th consumer of ARIA_LLM_URL. It does not build the
    # /chat/completions URL itself (OpenAICompatProvider does, generically), so
    # the join-regexes above won't catch it — instead it must derive its base
    # via normalise_base, never a bare .rstrip("/") on the env value.
    _bare_rstrip_base = re.compile(r'base_url\s*=\s*_aria_llm_url\.rstrip')

    for rel in (
        "llm/resilience.py",
        "llm/aria_llm_provider.py",
        "intel/self_healing.py",
        "llm/fallback.py",
    ):
        src = (root / rel).read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(src.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue  # comments describe the old bug on purpose
            if (
                _fstring_join.search(line)
                or _concat_join.search(line)
                or _bare_rstrip_base.search(line)
            ):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert offenders == [], (
        "these lines rebuild the ARIA-LLM URL by hand instead of using "
        "llm/aria_llm_url.py — that is the R-F2641 bug class:\n"
        + "\n".join(offenders)
    )
