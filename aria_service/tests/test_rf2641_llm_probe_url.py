"""R-F2641 — LLMHealthChecker probed /v1/v1/chat/completions (doubled /v1).

Live symptom (aria-intel, 2026-07-16 07:16Z, build b84812cc), every ~10s:

    POST https://<pod>-8888.proxy.runpod.net/v1/v1/chat/completions
        "HTTP/1.1 404 Not Found"

`ARIA_LLM_URL` carries the ``/v1`` suffix **by convention** — the
aria_llm_provider docstring documents it as ``https://aria-llm.runpod.io/v1``
and the live fly secret ends in ``/v1``. Every other caller therefore appends
only the method path:

    aria_llm_provider.py:159/:276 -> f"{base}/chat/completions"
    self_healing.py:232           -> base.rstrip("/") + "/models"

`resilience.py:208` was the lone outlier: it appended ``/v1/chat/completions``
to that same already-``/v1`` base, requesting ``/v1/v1/chat/completions``.

Why this matters beyond log noise: the probe 404s against a **healthy**
endpoint, so `_record_failure` trips the ``aria_llm`` circuit breaker and
`probe_status` reads "unhealthy" while the sovereign is UP — the exact
failure mode the R-F2566 comment 13 lines above :208 warns about
("report the sovereign as DOWN even when it is UP (breaking the
promotion-gate health signal)"). With GRPO Cycle 4 through the gate and
awaiting a promote call, a false DOWN would corrupt that promotion.

These tests drive the REAL broken path (`LLMHealthChecker._probe`) against a
fake vLLM that behaves like the live server (200 ONLY on the correct
``/v1/chat/completions``; 404 on anything else — verified live 2026-07-16),
and assert the user-visible outcome: a healthy sovereign reads healthy.
"""

from __future__ import annotations

from typing import Any

import pytest

from aria_service.llm import resilience

_SERVING_URL = "https://fake-pod.proxy.runpod.net/v1/chat/completions"


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    """Mimics the real vLLM / RunPod OpenAI-compatible server.

    Serves 200 ONLY on ``/v1/chat/completions``. Every other path 404s —
    which is precisely how the live endpoint behaves, so a doubled ``/v1``
    is indistinguishable here from the production failure.
    """

    requested_urls: list[str] = []

    def __init__(self, *_a: Any, **_kw: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False

    async def post(self, url: str, **_kwargs: Any) -> _FakeResponse:
        type(self).requested_urls.append(url)
        if url == _SERVING_URL:
            return _FakeResponse(200, '{"choices":[{"message":{"content":"ok"}}]}')
        return _FakeResponse(404, "Not Found")


@pytest.fixture
def fake_vllm(monkeypatch: pytest.MonkeyPatch) -> type[_FakeAsyncClient]:
    import httpx

    _FakeAsyncClient.requested_urls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


@pytest.mark.asyncio
async def test_probe_does_not_double_v1_when_url_carries_v1(
    fake_vllm: type[_FakeAsyncClient],
) -> None:
    """The live shape: ARIA_LLM_URL ends in /v1 (as documented + as deployed).

    Pre-fix this requested /v1/v1/chat/completions -> 404 -> unhealthy.
    """
    checker = resilience.LLMHealthChecker(
        endpoint="https://fake-pod.proxy.runpod.net/v1",
    )

    await checker._probe()

    assert fake_vllm.requested_urls == [_SERVING_URL], (
        f"probe hit the wrong URL: {fake_vllm.requested_urls}"
    )
    assert "/v1/v1/" not in fake_vllm.requested_urls[0]
    # User-visible outcome: a healthy sovereign must read healthy.
    assert checker.probe_status == "healthy"
    assert checker.consecutive_failures == 0


@pytest.mark.asyncio
async def test_probe_fails_closed_when_endpoint_omits_v1(
    fake_vllm: type[_FakeAsyncClient],
) -> None:
    """A no-/v1 endpoint must read UNHEALTHY, not be silently normalised.

    Never-false-clean. An earlier cut of this fix auto-appended the missing
    /v1, which made the probe green on a config the rest of the chain does not
    support: aria_llm_provider:159 POSTs to f"{base}/chat/completions" and
    self_healing:232 GETs f"{base}/models", both 404ing without the /v1. A green
    probe there would admit a DEAD provider to the chain head via
    is_available() — the probe must be UP iff the provider is UP.
    """
    checker = resilience.LLMHealthChecker(
        endpoint="https://fake-pod.proxy.runpod.net",
        failure_threshold=1,
    )

    await checker._probe()

    # Same URL aria_llm_provider would build — so probe and provider agree.
    assert fake_vllm.requested_urls == [
        "https://fake-pod.proxy.runpod.net/chat/completions"
    ]
    assert checker.probe_status == "unhealthy"


@pytest.mark.asyncio
async def test_probe_tolerates_trailing_slash(
    fake_vllm: type[_FakeAsyncClient],
) -> None:
    """A trailing slash must not resurrect the double-slash/404 class."""
    checker = resilience.LLMHealthChecker(
        endpoint="https://fake-pod.proxy.runpod.net/v1/",
    )

    await checker._probe()

    assert fake_vllm.requested_urls == [_SERVING_URL]
    assert checker.probe_status == "healthy"


@pytest.mark.asyncio
async def test_probe_still_reports_unhealthy_when_endpoint_is_genuinely_dead(
    fake_vllm: type[_FakeAsyncClient],
) -> None:
    """Never-false-clean: the fix must not paper over a REAL outage.

    This is today's actual production state — the pod is EXITED, so even the
    correct URL 404s (verified live 2026-07-16). The probe must still fail.
    """
    checker = resilience.LLMHealthChecker(
        endpoint="https://dead-pod.proxy.runpod.net/v1",
        failure_threshold=1,
    )

    await checker._probe()

    assert fake_vllm.requested_urls == [
        "https://dead-pod.proxy.runpod.net/v1/chat/completions"
    ]
    assert checker.probe_status == "unhealthy"
    assert checker.consecutive_failures == 1
