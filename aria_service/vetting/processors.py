"""R-F3151 — which processors may ever see vetting personal data.

── The defect this closes ────────────────────────────────────────────────
`documents.extract_document` called `call_structured` with `llm=None`, which
resolves to the application-wide chain. That chain is documented in
`llm/fallback.py:29` as "DD runs on Claude, EVERYTHING else on DeepSeek" — so
an applicant's passport, payslips and criminal-conviction context would have
been transmitted to a provider with no UK/EU adequacy decision.

For ordinary intelligence work that is a cost decision. Here it is not:

  * UK GDPR Art. 44-49 — a restricted transfer needs adequacy, SCCs or a
    derogation. None is in place for that provider.
  * Art. 10 / DPA 2018 Sch. 1 — criminal-offence data needs a specific
    domestic-law condition and an appropriate policy document.
  * Art. 28 — a processor must be engaged under contract with documented
    guarantees. An LLM reached because a chain degraded is not engaged.
  * Art. 5(1)(f) — integrity and confidentiality.

── Why this is fail-CLOSED and not a preference ──────────────────────────
`provider_scope()` (R-F2917) expresses a preference and the chain may still
degrade past it. A preference is the wrong instrument here: by the time a
degraded call returns, the personal data has already been transmitted. There
is no post-hoc check that un-sends it.

So this module builds a SINGLE approved provider with no chain behind it. If
no approved processor can be constructed, extraction does not happen at all:
the document is still stored and still routed to a human, which is a slower
outcome, never an unlawful one. Refusing to process is always available;
un-disclosing is not.

Configure with ARIA_VETTING_LLM_PROVIDERS (comma-separated, first
constructible wins). Adding a name here is a DATA-PROTECTION decision, not a
performance one — it asserts that a processor agreement and a transfer
mechanism exist for that vendor.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Anthropic only, by default. Not a quality judgement — it is the provider the
# platform already routes its most sensitive work to (R-F2917 pins DD to it).
_DEFAULT_APPROVED = "anthropic"

# Where each approved provider's credential lives. A provider with no entry
# here cannot be built, which is the correct default for an unreviewed vendor.
_API_KEY_ENV: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
}


@dataclass(frozen=True)
class ProcessorResolution:
    provider: Any | None
    name: str
    reason: str

    @property
    def usable(self) -> bool:
        return self.provider is not None


def approved_processors() -> list[str]:
    raw = os.getenv("ARIA_VETTING_LLM_PROVIDERS", _DEFAULT_APPROVED)
    return [p.strip().lower() for p in (raw or "").split(",") if p.strip()]


def resolve_vetting_processor() -> ProcessorResolution:
    """Build the first constructible APPROVED processor, or none at all.

    Never returns the application chain. A chain would reintroduce exactly the
    degradation this module exists to prevent.
    """
    from ..llm.factory import create_llm_provider

    names = approved_processors()
    if not names:
        return ProcessorResolution(
            None, "",
            "no approved processor is configured for vetting data "
            "(ARIA_VETTING_LLM_PROVIDERS is empty)")

    tried: list[str] = []
    for name in names:
        env_names = _API_KEY_ENV.get(name)
        if not env_names:
            tried.append(f"{name}: no credential mapping — not approvable here")
            continue
        api_key = ""
        for env_name in env_names:
            api_key = (os.getenv(env_name) or "").strip()
            if api_key:
                break
        if not api_key:
            tried.append(f"{name}: no API key configured")
            continue
        try:
            provider = create_llm_provider(name, api_key=api_key)
        except Exception as exc:  # noqa: BLE001 — never raise into an upload
            tried.append(f"{name}: construction failed ({exc})")
            continue
        if provider is None:
            tried.append(f"{name}: factory returned no provider")
            continue
        return ProcessorResolution(provider, name, "")

    return ProcessorResolution(
        None, "",
        "no approved processor is available for vetting data — "
        + "; ".join(tried))


def assert_served_by_approved(provider_name: str) -> bool:
    """Belt-and-braces: was the reply actually served by an approved processor?

    This CANNOT prevent a transfer — by the time it runs the data has gone —
    so it is a detector, not a control. It exists to make a silent regression
    (someone reintroducing the chain) loud rather than invisible.
    """
    served = (provider_name or "").strip().lower()
    if not served:
        return True          # nothing served; nothing was transferred
    return served in set(approved_processors())
