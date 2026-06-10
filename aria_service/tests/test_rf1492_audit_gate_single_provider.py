"""R-F1492 capability test — trust-audit readiness gate works single-provider.

The adversarial/security/constitution trust audits were gated on min_active=2 LLM
providers. ARIA runs SINGLE-provider (DeepSeek-only) by design, so the gate blocked
every run permanently — the adversarial safety data point froze ~05-27 (stale, not a
real score). R-F1492 lowers the bar to 1 while preserving the anti-poison intent:
a COOLING sole provider still skips, and run_weekly's all-empty/degraded guards still
protect the baseline.
"""
from unittest.mock import MagicMock, patch

from aria_service.autonomous.tasks import _audit_readiness_gate


def _mock_llm(active: bool = True):
    llm = MagicMock()
    llm.is_configured = True
    llm.get_stats.return_value = {
        "deepseek": {
            "cooldown_until": 0 if active else 9.0e18,
            "status": "ok" if active else "cooling_down",
            "last_kind": "chat",
        }
    }
    return llm


def test_single_active_provider_passes_now():
    with patch("aria_service.main.app") as app:
        app.state.llm_provider = _mock_llm(active=True)
        gate = _audit_readiness_gate("adversarial_weekly")  # default min_active=1 (R-F1492)
        assert gate["ok"] is True, f"1 active provider must pass now (R-F1492); got {gate}"


def test_old_min_active_2_would_block_single_provider():
    # Proves the frozen-since-05-27 bug: with one provider, min_active=2 always fails.
    with patch("aria_service.main.app") as app:
        app.state.llm_provider = _mock_llm(active=True)
        gate = _audit_readiness_gate("adversarial_weekly", min_active=2)
        assert gate["ok"] is False, "min_active=2 against a single provider blocks (the bug)"


def test_cooling_sole_provider_still_skips():
    # Anti-poison intent preserved: don't run a trust audit on a cooling LLM.
    with patch("aria_service.main.app") as app:
        app.state.llm_provider = _mock_llm(active=False)
        gate = _audit_readiness_gate("adversarial_weekly")
        assert gate["ok"] is False, "a cooling sole provider must still skip (anti-poison guard)"
