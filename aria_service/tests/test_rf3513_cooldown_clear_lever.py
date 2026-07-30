"""R-F3513 — a billing top-up had no way to take effect.

Operator paid for DeepSeek on 2026-07-30. The credit could not be used for ~18
hours, and nothing in the system could change that:

  * R-F678 sets a 24h HARD cooldown for a billing failure.
  * ``_mirror_cooldown_to_redis`` writes it to ``crucix:aria:llm:cooldown:<p>``
    with TTL = cooldown_until - now, and boot REHYDRATES it. Live evidence:
    "Provider deepseek_backup HARD cooldown (billing) rehydrated from Redis —
    56479s remaining".
  * ``_record_success`` is the only thing that clears it (fallback.py:236-239),
    and a cooling provider is never called — so the cooldown sustains itself.
  * ``/admin/state/key`` is GET-only, and no reset surface for provider stats
    exists anywhere.

CLAUDE.md §17 claimed the operator could "force-reset by setting the secret to
fresh billing and bouncing the machine, which clears in-memory stats AND the
redis-mirror TTL has already expired". Both halves are false: rehydration
re-applies the remaining cooldown, and the mirror TTL is pinned to the cooldown's
end so it has NOT expired. Acting on that line would have meant a 10-minute cold
boot that changed nothing. The doc is corrected in the same change.

This is a standing operational gap, not a one-off: it recurs on every top-up.

SECURITY NOTE, and the reason the gating test is here. Clearing a cooldown
re-enables a provider and therefore re-enables SPEND. ``_OPERATOR_ONLY_RE`` lists
SPECIFIC admin paths (admin/state/key, admin/purge) rather than all of /admin/,
so a new endpoint under /admin/ is NOT operator-gated by default — it would have
been reachable with the shared service token that aria-web and aria-wa hold.
"""
from __future__ import annotations

import re
import time

import pytest


def _operator_regex():
    src = open("aria_service/routes/aria.py", encoding="utf-8").read()
    m = re.search(r"_OPERATOR_ONLY_RE = re\.compile\((.*?)\n\)", src, re.S)
    assert m, "_OPERATOR_ONLY_RE not found"
    return re.compile("".join(re.findall(r'r"([^"]*)"', m.group(1))))


class TestTheEndpointIsOperatorTier:

    def test_cooldown_clear_requires_the_operator_token(self):
        rx = _operator_regex()
        assert rx.search("/api/aria/admin/llm/cooldown/clear"), (
            "the cooldown-clear endpoint is NOT operator-gated — the shared "
            "service token held by aria-web/aria-wa could re-enable provider spend"
        )

    def test_a_read_only_path_is_not_accidentally_gated(self):
        """Sanity: the pattern addition must not sweep unrelated routes."""
        rx = _operator_regex()
        assert not rx.search("/api/aria/health")
        assert not rx.search("/api/aria/news/recent")


class _FakeProvider:
    def __init__(self, name):
        self.name = name
        self.is_configured = True


def _chain(names=("deepseek", "anthropic")):
    from aria_service.llm import fallback as fb
    c = fb.FallbackProvider.__new__(fb.FallbackProvider)
    c.providers = [_FakeProvider(n) for n in names]
    c._stats = {n: {"calls": 0, "failures": 0, "cooldown_until": 0,
                    "last_kind": "", "last_failure": 0} for n in names}
    c._reset_chain_outcome()
    return c


class TestClearingRestoresTheProvider:

    def test_a_cooled_provider_is_serveable_again_without_a_restart(self, monkeypatch):
        chain = _chain()
        chain._stats["deepseek"]["cooldown_until"] = time.time() + 86400
        chain._stats["deepseek"]["last_kind"] = "billing"
        chain._stats["deepseek"]["failures"] = 5
        assert "deepseek" not in chain.get_health()["active_providers"]

        cleared = []
        monkeypatch.setattr(chain, "_clear_redis_cooldown",
                            lambda n: cleared.append(n), raising=False)
        out = chain.clear_cooldown("deepseek")

        assert out["cleared"] is True, out
        assert "deepseek" in chain.get_health()["active_providers"], (
            "the provider is still cooling after an explicit clear"
        )
        assert chain._stats["deepseek"]["failures"] == 0
        assert cleared == ["deepseek"], (
            "the Redis mirror was not deleted — boot would rehydrate the cooldown"
        )

    def test_clearing_an_unknown_provider_is_honest(self, monkeypatch):
        chain = _chain()
        monkeypatch.setattr(chain, "_clear_redis_cooldown",
                            lambda n: None, raising=False)
        out = chain.clear_cooldown("not_a_provider")
        assert out["cleared"] is False
        assert out.get("reason"), "a refusal must say why"

    def test_clearing_a_provider_that_was_not_cooling_is_not_a_lie(self, monkeypatch):
        """It must not report that it undid something it did not undo."""
        chain = _chain()
        monkeypatch.setattr(chain, "_clear_redis_cooldown",
                            lambda n: None, raising=False)
        out = chain.clear_cooldown("deepseek")
        assert out["was_cooling"] is False
        assert "deepseek" in chain.get_health()["active_providers"]

    def test_clear_all_reports_each_provider(self, monkeypatch):
        chain = _chain()
        chain._stats["deepseek"]["cooldown_until"] = time.time() + 3600
        monkeypatch.setattr(chain, "_clear_redis_cooldown",
                            lambda n: None, raising=False)
        out = chain.clear_cooldown("")
        assert out["cleared"] is True
        assert set(out["providers"]) == {"deepseek", "anthropic"}


class TestTheDocIsCorrected:

    def test_the_wrong_guidance_is_gone_from_where_it_actually_lived(self):
        """Corrected mid-change: I first reported this text as CLAUDE.md §17.
        It was a CODE COMMENT in llm/fallback.py. Assert against the REAL
        location, or the guard passes while the wrong advice survives."""
        src = open("aria_service/llm/fallback.py", encoding="utf-8").read()
        assert "halves are FALSE" in src, (
            "fallback.py still tells the operator that bouncing the machine "
            "clears a billing cooldown. It does not - boot rehydrates it."
        )
        doc = open("CLAUDE.md", encoding="utf-8").read()
        assert "Restarting does NOT clear it" in doc, (
            "CLAUDE.md does not warn that a restart cannot clear a cooldown"
        )

    def test_claude_md_points_at_the_real_lever(self):
        doc = open("CLAUDE.md", encoding="utf-8").read()
        assert "cooldown/clear" in doc, (
            "CLAUDE.md does not tell the operator how to actually act on a top-up"
        )
