"""
R-F1319 capability tests — learning/ package wiring.

Tests:
  1. All 12 learning modules have wire_success at module level
  2. All 12 learning modules compile without SyntaxError
"""
from __future__ import annotations

import ast
import os


LEARNING_MODULES = [
    "bookmarks.py",
    "fsrs_scheduler.py",
    "knowledge_spider.py",
    "learning_controller.py",
    "memory_replication.py",
    "metacognitive_journal.py",
    "output_harvester.py",
    "reading_queue.py",
    "research_engine.py",
    "style_learner.py",
    "training_export.py",
    "verification_gate.py",
]


def test_all_modules_compile():
    """Every learning module must compile without SyntaxError."""
    for f in LEARNING_MODULES:
        path = os.path.join("aria_service", "learning", f)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        try:
            ast.parse(source)
        except SyntaxError as e:
            raise AssertionError(f"SyntaxError in {f}:{e.lineno}: {e.msg}")


# R-F4042 (C-104) — learning modules that emit ONLY a `learning.<stem>` name
# whose stem is NOT in the brain registry. Their sole signal is the import-time
# wire, so removing it would leave them genuinely dark; they need real
# work-path wiring AND a registry entry. Tracked here so the gap is named
# rather than invisible. SHRINK-ONLY: adding to this list means a module went
# backwards.
IMPORT_ONLY_UNREGISTERED = {
    "bookmarks.py",
    "fsrs_scheduler.py",
    "learning_controller.py",
    "output_harvester.py",
    "reading_queue.py",
}


def test_all_modules_emit_a_brain_signal():
    """Every learning module must reach the brain — §21a, R-F1319's real intent.

    R-F4042 STRENGTHENED THIS. It used to assert the substring "wire_success"
    appeared anywhere in the file, which a module-level "X active" wire
    satisfied by being IMPORTED. That is not health, and it was emitted under
    `learning.<stem>` — a name `_MODULE_TOPICS` does not contain, so it was
    invisible to `never_seen` while the registered name sat there permanently
    critical (live: `learning.knowledge_spider` healthy, `knowledge_spider`
    never seen, and the latter is LOAD-BEARING).

    The contract is now: emit a signal, and — where the registry knows the
    module — emit it under THAT name, not a namespaced twin.
    """
    import re
    from aria_service.intel.brain_hook import _MODULE_TOPICS

    for f in LEARNING_MODULES:
        path = os.path.join("aria_service", "learning", f)
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()

        names = set(re.findall(r'module\s*=\s*"([^"]+)"', source))
        assert names, f"{f} emits no brain signal at all — it is dark (§21a)"

        stem = f[:-3]
        if stem in _MODULE_TOPICS:
            assert stem in names, (
                f"{f} is registered as {stem!r} but never reports under that "
                f"name (emits {sorted(names)}) — its gauge can never go green"
            )
            assert f"learning.{stem}" not in names, (
                f"{f} still emits the phantom 'learning.{stem}' — health "
                f"recorded where `never_seen` cannot see it (C-104)"
            )
        else:
            assert f in IMPORT_ONLY_UNREGISTERED, (
                f"{f} reports {sorted(names)} but {stem!r} is not in "
                f"_MODULE_TOPICS — register it, or add it to "
                f"IMPORT_ONLY_UNREGISTERED with a reason"
            )
