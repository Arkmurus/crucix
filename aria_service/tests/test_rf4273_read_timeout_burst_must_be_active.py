"""R-F4273 / C-233 - a store burst that ENDED was reported as an ongoing outage.

MEASURED LIVE on aria-intel at build_rev c58871a7, straight after a deploy. `/health`
said::

    status           : degraded
    degraded_reasons : ['state_backend_read_timeouts', ...]
    state_backend    : {"backend":"sqlite","reachable":true,"status":"amber",
                        "read_timeouts":{"count":6,"distinct_keys":5,
                                         "last_age_s":588.5,"degraded":true}}

Sampled three times over 90 seconds, the numbers said the opposite of the verdict:

    t=20:02:11  count=6  last_age_s=588.5
    t=20:02:56  count=6  last_age_s=633.9
    t=20:03:42  count=6  last_age_s=679.4

`count` frozen at 6; `last_age_s` climbing in lockstep with wall time. All six
happened in ONE burst during boot warmup, and nothing had timed out for ten
minutes - yet the service was still held at `degraded`.

THE STORE WAS NOT SLOW, and that had to be established before touching the verdict.
Measured in-machine against the live 630MB / 577,346-row database, read-only:

    crucix:aria:error_log            len=72455  read=0.3ms
    crucix:aria:brain_hook:stats     len= 9108  read=0.0ms
    crucix:dd:last_signal_check      len=   18  read=0.0ms
    aria:atlas:index:families        len= 3409  read=0.0ms
    crucix:aria:collab:cursor:aria   len=    3  read=0.0ms

Every key that "timed out" at 5s reads in under a millisecond. The fattest values in
the whole store (`neural_edges` 12.59MB, `intel_ledger` 8.21MB) fetch in 45ms and
16ms, so head-of-line blocking on a shared point lane is out too; `_READ_FLUSH_BUDGET_S`
is 0.3s, so the read-path write flush cannot reach 5s; CPU and memory PSI are flat at
0.00; and the current process had produced ZERO wedge stall dumps, so the loop never
stalled the 5s that would fire a `wait_for` on its own.

THE DEFECT IS THE RULE, NOT THE THRESHOLD. `degraded` was
``len(recent) >= _READ_TIMEOUT_DEGRADE_AT`` over a 900s window - volume only, with no
notion of whether the burst is still ARRIVING. Six timeouts ten minutes ago and six
timeouts happening right now are the same number, and only one of them is an outage.

THIS IS NOT MOVING A GAUGE TO SWITCH OFF A WARNING LIGHT (§1). Nothing is hidden and
no threshold is lowered: `count`, `distinct_keys`, `keys_sample` and `last_age_s` are
still reported for the full 15 minutes - that forensic memory is exactly what R-F4107
(C-140) added and it is untouched - and `state_backend.status` stays amber. What
changes is only whether a condition that has demonstrably STOPPED keeps the whole
service pinned at `degraded`. The evidence that it stopped is the same evidence class
that set it, which is the C-41 rule: a burst is over when reads start answering again.

The second axis matters for the same reason R-F3873 kept `quarantined` and `blocked`
apart: a burst during the ~10-minute heavy boot (§11c) and a burst in steady state are
DIFFERENT findings that demand different responses, and the old rule could not tell
them apart. `during_boot_only` labels it rather than suppressing it.
"""
from __future__ import annotations

import time

import pytest

from aria_service.intel import state_store as ss


@pytest.fixture(autouse=True)
def _clean():
    ss._reset_read_timeouts_for_test()
    yield
    ss._reset_read_timeouts_for_test()


def _seed(ages_s: list[float]) -> None:
    """Record timeouts that happened `age` seconds ago."""
    now = time.monotonic()
    ss._read_timeouts.extend((now - a, f"k{i}") for i, a in enumerate(ages_s))


def test_a_burst_that_stopped_is_no_longer_a_degradation():
    """THE CAPABILITY TEST - reproduces the live reading exactly.

    Six timeouts, the most recent 588 seconds ago, none since.
    """
    _seed([600.0, 597.0, 595.0, 592.0, 590.0, 588.0])
    r = ss.read_timeout_report()
    assert r["count"] == 6, "the forensic record must survive — C-140"
    assert r["last_age_s"] >= 500, "the age must still be reported"
    assert r["degraded"] is False, (
        "six timeouts that stopped ten minutes ago held the whole service at "
        f"'degraded': {r!r}"
    )


def test_a_burst_still_arriving_is_still_a_degradation():
    """The signal must survive the fix. This is the case R-F4107 was built for.

    A live outage keeps producing timeouts, so `last_age_s` stays small — which is
    precisely what distinguishes it from the recovered case above.
    """
    _seed([40.0, 30.0, 20.0, 10.0, 5.0, 1.0])
    r = ss.read_timeout_report()
    assert r["degraded"] is True, f"a live burst was not reported: {r!r}"
    assert r["active"] is True


def test_a_blip_is_still_not_a_degradation():
    """The volume threshold is unchanged — four is still noise, however recent."""
    _seed([4.0, 3.0, 2.0, 1.0])
    assert ss.read_timeout_report()["degraded"] is False


def test_volume_alone_cannot_degrade_and_recency_alone_cannot_either():
    """Both conditions are required, and each is proven necessary on its own."""
    _seed([600.0] * 20)                       # lots, but long over
    assert ss.read_timeout_report()["degraded"] is False
    ss._reset_read_timeouts_for_test()
    _seed([1.0])                              # right now, but one
    assert ss.read_timeout_report()["degraded"] is False


def test_the_forensic_record_is_never_suppressed():
    """Nothing is hidden — the whole point of not 'switching off the warning light'."""
    _seed([600.0, 597.0, 595.0, 592.0, 590.0, 588.0])
    r = ss.read_timeout_report()
    assert r["distinct_keys"] == 6 and len(r["keys_sample"]) == 6, (
        f"the keys that went dark must still be named: {r!r}"
    )


def test_a_boot_window_burst_is_labelled_not_merged():
    """Two axes, kept apart (the R-F3873 rule).

    A burst during the ~10-minute heavy boot (§11c) and one in steady state demand
    different responses; a reader must be able to tell which they are looking at.
    """
    _seed([600.0, 597.0, 595.0])
    assert ss.read_timeout_report()["during_boot_only"] is True


def test_an_unmeasurable_report_is_never_reported_as_healthy():
    """C-96 — 'could not measure' must not render as 'measured and fine'."""
    r = ss.read_timeout_report(window_s=float("nan"))
    assert r["degraded"] is False and r.get("unmeasurable") or r["count"] == 0
