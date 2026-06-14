"""R-F1567 — Capability test: the self-coder stage guard's symbol-preservation
check now protects MORE than top-level public defs.

Before R-F1567 the AST collector (R-F1285/R-F1450) only diffed top-level PUBLIC
functions/classes. A self-coded "fix" that gutted a PRIVATE module-level helper
(_prefixed) or a CLASS METHOD body — without dropping a public top-level def line
— passed the gate; only the separate 50%-line truncation guard might have caught
large deletions. R-F1567 widens the protected set to include:
  (a) methods defined directly inside top-level classes (qualified ClassName.method)
  (b) module-level private functions (_prefixed)

These tests drive the REAL `stage_improvement` path (the path the autonomous
coder uses) and assert the user-visible outcome: a proposal that drops a method
or a private function is NOT staged, while an additive proposal still stages.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.self_improve as si


# A current file with: a public class holding two methods, a private helper, and
# a public function. Padded so the proposals stay ABOVE the 50%-line guard,
# isolating the AST symbol check as the thing under test.
_CURRENT = (
    "import os\n\n\n"
    "class Engine:\n"
    "    def start(self):\n"
    "        # do a bunch of work\n"
    "        x = 1\n"
    "        y = 2\n"
    "        return x + y\n\n"
    "    def stop(self):\n"
    "        # tear everything down\n"
    "        a = 3\n"
    "        b = 4\n"
    "        return a - b\n\n\n"
    "def _private_helper():\n"
    "    # internal-only utility\n"
    "    v = 10\n"
    "    return v * 2\n\n\n"
    "def public_entry():\n"
    "    # the public entry point\n"
    "    return Engine().start() + _private_helper()\n"
)


def _stage(tmp_path, monkeypatch, rel: str, current: str, proposed: str):
    monkeypatch.setattr(si, "_root", tmp_path)
    monkeypatch.setattr(si, "MODIFIABLE_FILES", set(si.MODIFIABLE_FILES) | {rel})
    (tmp_path / rel).write_text(current, encoding="utf-8")
    return asyncio.run(si.stage_improvement(rel, proposed, "bug_fix", "test fix"))


def test_rf1567_rejects_dropping_a_class_method(tmp_path, monkeypatch):
    """Dropping Engine.stop (keeps the public class + every top-level public def)
    must now be rejected — pre-R-F1567 this slipped through."""
    # Engine keeps `start` but loses `stop`; all top-level public symbols (Engine,
    # public_entry) and the private helper are still present, and lines stay >50%.
    proposed = (
        "import os\n\n\n"
        "class Engine:\n"
        "    def start(self):\n"
        "        x = 1\n"
        "        y = 2\n"
        "        return x + y\n\n\n"
        "def _private_helper():\n"
        "    v = 10\n"
        "    return v * 2\n\n\n"
        "def public_entry():\n"
        "    return Engine().start() + _private_helper()\n"
    )
    res = _stage(tmp_path, monkeypatch, "rf1567_method.py", _CURRENT, proposed)
    assert res.get("truncation_guard") is True, res
    assert res.get("staged") is False, res
    assert "Engine.stop" in res.get("error", ""), res


def test_rf1567_rejects_dropping_a_private_function(tmp_path, monkeypatch):
    """Dropping the module-level private helper must now be rejected."""
    # _private_helper removed; Engine (both methods) + public_entry kept.
    proposed = (
        "import os\n\n\n"
        "class Engine:\n"
        "    def start(self):\n"
        "        x = 1\n"
        "        y = 2\n"
        "        return x + y\n\n"
        "    def stop(self):\n"
        "        a = 3\n"
        "        b = 4\n"
        "        return a - b\n\n\n"
        "def public_entry():\n"
        "    return Engine().start()\n"
    )
    res = _stage(tmp_path, monkeypatch, "rf1567_priv.py", _CURRENT, proposed)
    assert res.get("truncation_guard") is True, res
    assert res.get("staged") is False, res
    assert "_private_helper" in res.get("error", ""), res


def test_rf1567_allows_adding_a_method(tmp_path, monkeypatch):
    """A benign proposal that ADDS a method (and keeps everything else) must stage."""
    proposed = _CURRENT.replace(
        "    def stop(self):\n"
        "        # tear everything down\n"
        "        a = 3\n"
        "        b = 4\n"
        "        return a - b\n",
        "    def stop(self):\n"
        "        # tear everything down\n"
        "        a = 3\n"
        "        b = 4\n"
        "        return a - b\n\n"
        "    def pause(self):\n"
        "        # new capability\n"
        "        return True\n",
    )
    assert proposed != _CURRENT, "fixture replacement did not apply"
    res = _stage(tmp_path, monkeypatch, "rf1567_add.py", _CURRENT, proposed)
    assert res.get("truncation_guard") is not True, res
    assert res.get("staged") is True, res


def test_rf1567_still_rejects_dropping_a_public_symbol(tmp_path, monkeypatch):
    """The original R-F1285/R-F1450 public-symbol-drop case still rejects."""
    # public_entry (top-level public function) removed.
    proposed = (
        "import os\n\n\n"
        "class Engine:\n"
        "    def start(self):\n"
        "        x = 1\n"
        "        y = 2\n"
        "        return x + y\n\n"
        "    def stop(self):\n"
        "        a = 3\n"
        "        b = 4\n"
        "        return a - b\n\n\n"
        "def _private_helper():\n"
        "    v = 10\n"
        "    return v * 2\n"
    )
    res = _stage(tmp_path, monkeypatch, "rf1567_pub.py", _CURRENT, proposed)
    assert res.get("truncation_guard") is True, res
    assert res.get("staged") is False, res
    assert "public_entry" in res.get("error", ""), res
