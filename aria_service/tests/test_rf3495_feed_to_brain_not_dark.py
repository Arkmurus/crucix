"""R-F3495 — _feed_to_brain swallowed every downstream failure.

Three failure branches inside ``news_monitor._feed_to_brain`` did nothing but
``logger.debug``:

    except Exception as e:  logger.debug("brain feed failed: %s", e)
    except Exception:       logger.debug("intel_ledger feed failed", exc_info=True)
    except Exception:       logger.debug("vault-source brain absorb failed", exc_info=True)

CLAUDE.md §21a is explicit that this is DARK, not wired: a path counts as wired
only if BOTH its success and failure branches reach brain_hook / capability_gaps
/ mistake_ledger / a metric — and "logged to console / except: pass" is named as
the counter-example. At debug level these lines are not even emitted in
production.

Consequence: an article could be archived, counted as "new", and reported as
promoted while its ledger entry and its brain absorption both failed. The poll
summary answered "how many new URLs did I see", never "how many became usable
knowledge" (§25).

It also corrupted the stage record R-F3486 added. Because the failures are
swallowed INSIDE _feed_to_brain, nothing propagates, so _ingest_article marked
``brain_absorbed ok=True`` even when the ledger write had failed — a green stage
over a failed write, which is worse than no stage at all.
"""
from __future__ import annotations

import pytest

from aria_service.intel import news_monitor as nm, news_archive

# R-F3770/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(news_archive, "_DB_PATH", tmp_path / "news_archive.db")
    news_archive._reset_for_tests()
    yield
    news_archive._reset_for_tests()


def _article():
    return {"url": "https://janes.com/a1",
            "title": "Poland signs defence procurement contract",
            "summary": "Defence ministry tender award for air defence systems.",
            "source": "Janes", "category": "global_defence", "tier": "1A"}


class TestDownstreamFailuresReachTheBrain:

    @pytest.mark.asyncio
    async def test_ledger_failure_is_wired_not_swallowed(self, monkeypatch):
        captured = []
        monkeypatch.setattr(nm, "wire_failure",
                            lambda **kw: captured.append(kw), raising=False)

        async def _boom(*_a, **_kw):
            raise RuntimeError("ledger unavailable")

        from aria_service.intel import intel_ledger
        monkeypatch.setattr(intel_ledger, "add_signal", _boom, raising=False)
        await nm._feed_to_brain(_article())

        assert any("ledger" in str(c.get("detail", "")).lower() for c in captured), (
            f"intel_ledger failure never reached the brain: {captured}"
        )

    @pytest.mark.asyncio
    async def test_brain_feed_failure_is_wired_not_swallowed(self, monkeypatch):
        """_feed_to_brain absorbs via wire_success(), not brain_hook.absorb —
        checked rather than assumed (§3b). Its failure was the first of the
        three debug-only branches."""
        captured = []
        monkeypatch.setattr(nm, "wire_failure",
                            lambda **kw: captured.append(kw), raising=False)

        def _boom(**_kw):
            raise RuntimeError("brain unavailable")

        monkeypatch.setattr(nm, "wire_success", _boom, raising=False)
        await nm._feed_to_brain(_article())

        assert any("brain_feed" in str(c.get("source", "")) for c in captured), (
            f"a brain-feed failure produced no signal: {captured}"
        )

    def test_no_failure_branch_is_debug_only(self):
        """Guard the CLASS: §21a forbids a failure branch whose only output is a
        debug log. Applies to _feed_to_brain, where all three lived."""
        import ast, inspect, textwrap
        src = textwrap.dedent(function_source(nm, "_feed_to_brain"))
        tree = ast.parse(src)
        offenders = []
        for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)):
            wired = False
            for call in ast.walk(handler):
                if not isinstance(call, ast.Call):
                    continue
                name = (getattr(call.func, "id", "")
                        or getattr(call.func, "attr", ""))
                # _wire_article_stage_failure is the sanctioned shared sink
                # added by R-F3495; it wires AND records the archive stage.
                if name in ("wire_failure", "record_gap", "mark_stage",
                            "wire_success", "_wire_article_stage_failure"):
                    wired = True
            if not wired:
                offenders.append(f"except handler at line {handler.lineno} "
                                 f"reaches no brain sink")
        assert not offenders, (
            "dark failure branch in _feed_to_brain (§21a):\n  " + "\n  ".join(offenders)
        )


class TestStageRecordReflectsRealOutcome:

    @pytest.mark.asyncio
    async def test_a_swallowed_ledger_failure_does_not_mark_the_stage_green(
            self, monkeypatch):
        """R-F3486 marked brain_absorbed ok=True whenever nothing propagated.
        A green stage over a failed write is worse than no stage."""
        from aria_service.intel import intel_ledger

        async def _boom(*_a, **_kw):
            raise RuntimeError("ledger unavailable")

        monkeypatch.setattr(intel_ledger, "add_signal", _boom, raising=False)
        monkeypatch.setattr(nm, "_store_article", _noop, raising=False)
        monkeypatch.setattr(nm, "_mark_seen", _noop_1, raising=False)

        res = await nm._ingest_article(_article())
        assert res["archived"] is True
        rec = await news_archive.get_article(res["article_id"])
        stages = rec.get("stages") or {}
        assert "ledger_written" in stages, (
            f"the ledger outcome was never recorded at all: {sorted(stages)}"
        )
        assert stages["ledger_written"]["ok"] is False, (
            "a failed ledger write was recorded as successful"
        )


async def _noop(*_a, **_kw):
    return None


async def _noop_1(_x):
    return None
