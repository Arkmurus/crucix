"""C-38 / R-F3926 — C-30/C-31 left M3 unable to recover and M4 unable to fail.

Found by the high-effort review of the C-29..C-36 fixes. Both defects are in R-F3909,
and both are the SAME MISTAKE THAT R-F3909 SET OUT TO FIX, displaced by one step.

FINDING 3 — M3 POISONS ITS OWN INPUT AND LATCHES RED FOREVER.
`check_wa_connection_health` counts gaps in `crucix:aria:capability_gaps` whose text
contains `auth_lost` or `disconnect`. C-30 rewrote its failure branch to emit:

    "WA connection health: N auth_lost and M disconnected signals in capability_gaps
     — the listener has been dropping"

`record_gap` stores that text INTO THE LIST THE CHECK READS. So one genuine WA drop —
or any unrelated gap mentioning "disconnect", e.g. a store `connection disconnected`
error — makes `observed > 0`, and from then on the monitor's own output keeps
`observed > 0` forever. The detail string changes every hour (the counts grow), so the
1-hour dedupe never suppresses it; after ~51 hours the panel reads "51 auth_lost and
51 disconnected signals" for a WhatsApp listener that has been stable for weeks, and
the coder loop is fed a perpetual phantom `engine_failure`.

C-30 also deleted the `wire_success` branch, so **no code path can emit a healthy M3
signal**. Before C-30 the monitor was inverted; after C-30 it is a one-way latch. A
guard that cannot go green is as useless as one that cannot go red — that is C-30's
own stated principle, applied to C-30.

FINDING 2 — M4 CANNOT REPORT A FAILURE IT IS FULLY CAPABLE OF DETECTING.
C-31's early return fires whenever any inspected file is unreadable, which on
aria-intel is ALWAYS (the three Node-tier files are absent by design). It returns
before the verdict block, discarding `endpoint_exists` — a check performed against
`routes/aria.py`, which IS present in the image and needs no Node file at all.

So if `/api/aria/brain/signal` were removed or renamed, M4 would compute
`endpoint_exists = False`, throw it away, emit nothing, and `run_all_checks` would
fold `None` into `(m4_healthy or m4_unknown)` and report `composite_health: healthy`
while the brain-signal endpoint was gone. C-31 correctly stopped M4 asserting a
failure it could not see, and accidentally stopped it reporting one it could.

THE SHAPE, IN BOTH: a fix for "asserts what it cannot measure" that overshot into
"cannot report what it CAN measure". The cure is per-check honesty — report each
component at the confidence it individually earns — not a blanket verdict either way.
"""
from __future__ import annotations

import pytest

from aria_service.intel import wiring_monitor as wm


class _Rec:
    def __init__(self) -> None:
        self.failures: list[dict] = []
        self.successes: list[dict] = []


@pytest.fixture
def verdicts(monkeypatch):
    rec = _Rec()
    monkeypatch.setattr(wm, "wire_failure", lambda **kw: rec.failures.append(kw))
    monkeypatch.setattr(wm, "wire_success", lambda **kw: rec.successes.append(kw))
    return rec


class _Store:
    def __init__(self, gaps):
        self._gaps = gaps

    async def lrange(self, key, start, stop):
        return self._gaps


# ─────────────────────────────────────────────────────────────────────────────
# FINDING 3 — M3 must not count its own output, and must be able to recover.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_m3_does_not_count_its_own_failure_gap(monkeypatch, verdicts) -> None:
    """THE LATCH: the monitor's own emitted text must not be evidence to itself.

    This is the exact string C-30 emitted, fed back as the only gap in the list.
    """
    # The REAL serialised shape. `record_gap` writes `source`, NOT `module`
    # (capability_gaps entry fields), and this check is wired with
    # source="wiring_monitor:check_wa_connection_health". A fixture inventing a
    # `module` key would let a skip that matches nothing in production pass here.
    own_output = (
        '{"id": "abc", "type": "engine_failure", '
        '"detail": "WA connection health: 3 auth_lost and 2 disconnected signals '
        'in capability_gaps \\u2014 the listener has been dropping", '
        '"source": "wiring_monitor:check_wa_connection_health", '
        '"resolved": false}'
    )
    monkeypatch.setattr(wm, "rs", _Store([own_output]))

    result = await wm.check_wa_connection_health()

    assert result["wa_auth_lost_signals"] == 0 and result["wa_disconnected_signals"] == 0, (
        "C-38: M3 counted its OWN gap as evidence of a WA drop — it latches red forever"
    )
    assert not verdicts.failures, "self-generated evidence produced a failure verdict"


@pytest.mark.asyncio
async def test_m3_can_return_to_healthy_after_a_real_drop(monkeypatch, verdicts) -> None:
    """RECOVERY: once the real signals age out, M3 must stop reporting failure.

    C-30 deleted the success branch, so nothing could ever clear it.
    """
    monkeypatch.setattr(wm, "rs", _Store([]))

    result = await wm.check_wa_connection_health()

    assert not verdicts.failures
    assert result.get("determinate") is False


@pytest.mark.asyncio
async def test_m3_still_reports_a_genuine_third_party_drop(monkeypatch, verdicts) -> None:
    """AND IT MUST STILL BITE — a real WA gap from the listener is still a failure."""
    real = '{"module": "wa_listener", "gap_type": "wa_auth_lost", "detail": "loggedOut"}'
    monkeypatch.setattr(wm, "rs", _Store([real]))

    result = await wm.check_wa_connection_health()

    assert result["wa_auth_lost_signals"] == 1
    assert verdicts.failures, "a genuine WA auth-loss no longer raises a failure"


# ─────────────────────────────────────────────────────────────────────────────
# FINDING 2 — M4 must report what it CAN see, even when part is unreadable.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_m4_reports_a_missing_endpoint_even_when_node_files_are_absent(
    monkeypatch, verdicts
) -> None:
    """THE SYMPTOM: the Python-side check is fully determinate and was discarded.

    routes/aria.py is present in the image; the Node files are not. A removed
    brain/signal route must still raise, or the monitor is blind to a regression it
    is perfectly able to detect.
    """
    def _src(path: str):
        if path.endswith("aria.py"):
            # Deliberately contains NEITHER token the check greps for — an earlier
            # draft of this stub said "the brain/signal route has been removed",
            # which of course contains 'brain/signal' and made the check pass.
            return '@router.get("/health")\nasync def health_ep(): ...', True
        return "", False        # Node tier absent, exactly as in production

    monkeypatch.setattr(wm, "_read_source", _src)

    result = await wm.test_brain_signal_path()

    assert result.get("endpoint_exists") is False
    assert verdicts.failures, (
        "C-38: /api/aria/brain/signal is gone and M4 said nothing — the early return "
        "discarded the one check that needed no Node file"
    )
    assert result.get("path_healthy") is not True


@pytest.mark.asyncio
async def test_m4_stays_unknown_when_only_the_node_tier_is_unreadable(
    monkeypatch, verdicts
) -> None:
    """C-31's actual fix must survive: absent Node files are UNKNOWN, not broken."""
    def _src(path: str):
        if path.endswith("aria.py"):
            return '@router.post("/brain/signal")', True
        return "", False

    monkeypatch.setattr(wm, "_read_source", _src)

    result = await wm.test_brain_signal_path()

    assert result.get("inspectable") is False
    assert result.get("path_healthy") is None
    assert not verdicts.failures, (
        "absent Node files must not be reported as a broken brain wire (C-31)"
    )
