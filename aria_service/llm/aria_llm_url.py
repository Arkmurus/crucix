"""R-F2645 — the one place that knows how to build an ARIA-LLM URL.

WHY THIS MODULE EXISTS
Four call sites each re-invented the base/join against ``ARIA_LLM_URL``, and
they did not agree on whether the env var carries the ``/v1`` suffix:

    aria_llm_provider.py:159/:276  f"{base}/chat/completions"   # assumes /v1 present
    self_healing.py:232           base.rstrip("/") + "/models"  # assumes /v1 present
    fallback.py:594               base_url=url.rstrip("/")      # via OpenAICompatProvider
    resilience.py:208             f"{base}/v1/chat/completions" # assumed /v1 ABSENT

The odd one out shipped: the health probe requested ``/v1/v1/chat/completions``
and 404'd against a HEALTHY endpoint, tripping the ``aria_llm`` breaker and
reporting the sovereign DOWN while it was UP (R-F2641; the promotion-gate
corruption R-F2566 warns about). R-F2641 fixed that one caller. This module
fixes the CLASS (CLAUDE.md §1): the join now lives in exactly one place, so a
fourth caller cannot invent a fifth opinion.

THE CONTRACT — ``ARIA_LLM_URL`` INCLUDES ``/v1``
Established, not invented here: the aria_llm_provider docstring documents
``https://aria-llm.runpod.io/v1``, ``docs/aria_llm_v01_activation.md`` shows
that shape throughout, and the live fly secret ends in ``/v1``.

FAIL CLOSED — we deliberately do NOT auto-append a missing ``/v1``
A tolerant normaliser was written first and rejected in review. Auto-appending
would make the health probe read HEALTHY on a no-``/v1`` endpoint while the
provider POSTs to ``{base}/chat/completions`` and 404s on every real call — a
false-green that admits a dead provider to the chain head via
``is_available()``. Instead every caller builds the SAME url from the SAME
base, so the probe is UP iff the provider is UP, and a misconfigured endpoint
fails closed and visibly (never-false-clean).

``looks_v1_shaped`` exists so callers can SURFACE a misconfiguration rather
than silently 404 forever; it never rewrites the URL.
"""

from __future__ import annotations

_V1_SUFFIX = "/v1"


def normalise_base(raw: str) -> str:
    """Return ``raw`` with trailing slashes stripped — the canonical base.

    The single definition of "the ARIA-LLM base URL". Does not add or remove
    ``/v1``: the env var owns that (see module docstring).
    """
    return (raw or "").strip().rstrip("/")


def chat_completions_url(raw: str) -> str:
    """Build the OpenAI-compatible chat/completions URL for ``raw``."""
    return f"{normalise_base(raw)}/chat/completions"


def models_url(raw: str) -> str:
    """Build the OpenAI-compatible /models URL for ``raw``."""
    return f"{normalise_base(raw)}/models"


def looks_v1_shaped(raw: str) -> bool:
    """True when ``raw`` carries the ``/v1`` suffix the chain requires.

    Callers use this to REPORT a misconfiguration (log/gap), never to silently
    repair one — repairing it is what produces a false-green probe.
    """
    return normalise_base(raw).endswith(_V1_SUFFIX)
