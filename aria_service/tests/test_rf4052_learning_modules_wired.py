"""R-F4052 (C-108) — five learning modules ran, but their work was dark.

C-104 removed the import-time `wire_success(module="learning.x", summary="X
active")` phantoms, which asserted health from an `import` under a name the
brain registry does not know. For eleven modules that was safe: they already
emitted their registered name from a real work path.

Five did NOT, and were deliberately left alone at the time because deleting
their only signal would have made them genuinely dark:

    bookmarks · fsrs_scheduler · learning_controller · output_harvester · reading_queue

They are not dormant — each is driven (learning_controller from
`autonomous/tasks.py`, output_harvester from `coder_entrypoint`/routes,
reading_queue and bookmarks from routes, fsrs_scheduler from `intel/student.py`)
— so §21a applies squarely: a code path is wired only if BOTH its success and
failure branches reach the brain. Theirs reached nothing.

WHY A THROTTLED HELPER RATHER THAN FIVE COPIES. These are per-item functions
(`record_bookmark`, `mark_processed`, `review_topic`) called far more often than
once a cycle. An unthrottled success signal per call is the ledger flood this
repo has paid for repeatedly — `cost_tracker` and `grounding_reward` are exempt
from §21a for exactly that reason, `loop_monitor` (R-F3557) rate-limits both its
breach and healthy signals, and C-102 had to report on CHANGE for the same
cause. So the cooldown belongs in `engine_wiring`, next to the primitives it
throttles, where the next module gets it for free.

Failures are NOT throttled here: they are rare, and `wire_failure` already
routes through `capability_gaps.record_gap`, which dedupes 1h (R-F66).
"""
from __future__ import annotations

import pathlib
import re

from aria_service.intel import engine_wiring as ew


TARGETS = {
    "bookmarks": "aria_service/learning/bookmarks.py",
    "fsrs_scheduler": "aria_service/learning/fsrs_scheduler.py",
    "learning_controller": "aria_service/learning/learning_controller.py",
    "output_harvester": "aria_service/learning/output_harvester.py",
    "reading_queue": "aria_service/learning/reading_queue.py",
}


def _src(path: str) -> str:
    return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")


# ── the throttle primitive ────────────────────────────────────────────────

def test_throttled_success_emits_then_suppresses(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(ew, "wire_success", lambda **kw: sent.append(kw))
    ew._SUCCESS_LAST.clear()

    clock = {"t": 1000.0}
    monkeypatch.setattr(ew.time, "monotonic", lambda: clock["t"])

    assert ew.wire_success_throttled("m1", "first") is True
    assert ew.wire_success_throttled("m1", "second") is False, (
        "a per-item success signal must not emit on every call — that is the "
        "ledger-flood shape this helper exists to prevent"
    )
    assert len(sent) == 1


def test_throttled_success_emits_again_after_the_interval(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(ew, "wire_success", lambda **kw: sent.append(kw))
    ew._SUCCESS_LAST.clear()

    clock = {"t": 1000.0}
    monkeypatch.setattr(ew.time, "monotonic", lambda: clock["t"])

    assert ew.wire_success_throttled("m1", "a", min_interval_s=300.0) is True
    clock["t"] += 301.0
    assert ew.wire_success_throttled("m1", "b", min_interval_s=300.0) is True, (
        "a throttle that never re-opens is not a throttle — the module would "
        "go stale and read as dead"
    )
    assert len(sent) == 2


def test_throttle_is_per_module(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(ew, "wire_success", lambda **kw: sent.append(kw))
    ew._SUCCESS_LAST.clear()
    monkeypatch.setattr(ew.time, "monotonic", lambda: 1000.0)

    assert ew.wire_success_throttled("m1", "x") is True
    assert ew.wire_success_throttled("m2", "y") is True, (
        "one module's cooldown must not silence another's"
    )


def test_throttled_success_never_raises(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("brain down")

    monkeypatch.setattr(ew, "wire_success", _boom)
    ew._SUCCESS_LAST.clear()
    # Telemetry must never break the caller's work.
    assert ew.wire_success_throttled("m1", "x") is False


# ── the five modules ──────────────────────────────────────────────────────

def test_each_module_wires_both_branches():
    """§21a: success AND failure must reach the brain, under the module's own name."""
    missing = {}
    for mod, path in TARGETS.items():
        src = _src(path)
        has_success = bool(
            re.search(r'wire_success_throttled\(\s*\n?\s*"%s"' % mod, src)
            or re.search(r'wire_success\(\s*\n?\s*module\s*=\s*"%s"' % mod, src)
        )
        has_failure = bool(
            re.search(r'wire_failure\(\s*\n?\s*module\s*=\s*"%s"' % mod, src)
        )
        if not (has_success and has_failure):
            missing[mod] = {"success": has_success, "failure": has_failure}
    assert not missing, (
        "these learning modules do not reach the brain on both branches "
        f"(§21a): {missing}"
    )


def _code_only(src: str) -> str:
    """Source with whole-line comments stripped.

    The phantom name legitimately appears in the comment that explains why it
    was removed. Matching raw text would flag that prose — the same
    blunt-substring flaw that let the old R-F1319 tests pass on a mere
    `"wire_success" in source`.
    """
    return "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
    )


def test_no_module_reintroduces_the_phantom_name():
    """C-104 regression guard: never report under `learning.<stem>` again."""
    for mod, path in TARGETS.items():
        code = _code_only(_src(path))
        assert 'module="learning.%s"' % mod not in code, (
            f"{path} re-emits the phantom 'learning.{mod}' — a name the "
            f"registry does not know, invisible to never_seen"
        )
