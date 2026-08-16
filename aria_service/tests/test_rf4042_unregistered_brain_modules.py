"""R-F4042 (C-104) — brain stats could not tell a real module from a phantom.

THE DEFECT, in two halves.

**1. The health surface accepted any name.** `get_stats()` derives `never_seen`
as `_MODULE_TOPICS - reporting`, but nothing checked the other direction: a
module may report under ANY name and is silently added to `modules`. Because
`never_seen` is derived FROM the registry, an unregistered reporter can never
appear in it — so a phantom name is structurally invisible to the one gauge that
exists to spot missing modules.

**2. Health was asserted at import, under a namespaced name.** R-F1319/R-F1320
added a module-level `wire_success(module="learning.x", summary="X active")` to
61 files. It fires when the file is IMPORTED, so it proves the module was
imported, not that it works — and `learning.x` is not what the registry calls it.

Measured across the tree, 2026-08-16:

    files with an import-time wire : 61
    wire calls found              : 74
    names NOT in _MODULE_TOPICS   : 60

Live on aria-intel, the two halves combined into a gauge that read backwards:

    learning.knowledge_spider   IN STATS   total=2 success=2    (phantom)
    knowledge_spider            never_seen                       (registered,
                                                                  LOAD-BEARING)

R-F668 calls a never-seen load-bearing module "an install/wiring bug ...
critical". So the spider raised a permanent critical alert that no amount of
health could clear, while its real signal landed under a name nothing reads —
and had the spider actually died, nothing would have changed. A gauge that
reports the same thing whether the subject is alive or dead carries no
information: the §1 "certified by an absence" class, inverted.

WHY REMOVAL IS SAFE AND NOT A LOSS OF COVERAGE. Every one of the 11 modules
touched already emits its REGISTERED name from a real work path (verified per
file before editing, and re-asserted below). The module-level block was a pure
duplicate. Five other learning modules emit ONLY the phantom and are
deliberately NOT touched — removing theirs would leave them genuinely dark, so
they need real wiring, which is a separate piece of work.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from aria_service.intel import brain_hook as bh


# Modules that already emit their registered name from a work path.
GROUP1 = {
    "aria_service/learning/knowledge_spider.py": "knowledge_spider",
    "aria_service/learning/memory_replication.py": "memory_replication",
    "aria_service/learning/metacognitive_journal.py": "metacognitive_journal",
    "aria_service/learning/research_engine.py": "research_engine",
    "aria_service/learning/style_learner.py": "style_learner",
    "aria_service/learning/training_export.py": "training_export",
    "aria_service/learning/verification_gate.py": "verification_gate",
    "aria_service/writers/anti_corruption_law.py": "anti_corruption_law",
    "aria_service/writers/assessment_writer.py": "assessment_writer",
    "aria_service/writers/procurement_paper_writer.py": "procurement_paper_writer",
    "aria_service/writers/writer_orchestrator.py": "writer_orchestrator",
}


def _emitted_names(path: str) -> set[str]:
    src = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r'module\s*=\s*"([^"]+)"', src))


def test_is_registered_module_discriminates():
    known = set(bh._MODULE_TOPICS.keys())
    assert known, "registry is empty — every assertion below would be vacuous"

    assert bh._is_registered_module(next(iter(known))) is True
    assert bh._is_registered_module("learning.knowledge_spider") is False
    assert bh._is_registered_module("definitely_not_a_module_xyz") is False


@pytest.mark.parametrize("path,registered", sorted(GROUP1.items()))
def test_no_phantom_namespaced_signal_remains(path, registered):
    """The `pkg.module` twin must be gone..."""
    names = _emitted_names(path)
    phantoms = {n for n in names if "." in n and n.rsplit(".", 1)[1] == registered}
    assert not phantoms, (
        f"{path} still emits {phantoms} — a name the registry does not know, so "
        f"its health lands where `never_seen` cannot see it"
    )


@pytest.mark.parametrize("path,registered", sorted(GROUP1.items()))
def test_the_registered_name_is_still_emitted(path, registered):
    """...and the REAL signal must survive. Removal must not create a dark module."""
    assert registered in _emitted_names(path), (
        f"{path} no longer emits its registered name {registered!r} — removing "
        f"the phantom wire must never leave a module with no signal at all"
    )


def test_stats_expose_unregistered_reporters(monkeypatch):
    """The surface must NAME the phantoms, not merely count modules.

    Pinned as a capability of `get_stats`, not as a fixed list: the phantom set
    shrinks as modules are corrected, and a test hardcoding today's names would
    fail for exactly the right fix.
    """
    modules = {
        "real_one": {"total": 3},
        "learning.knowledge_spider": {"total": 2},
        "autonomous.tasks": {"total": 1},
    }
    monkeypatch.setattr(bh, "_MODULE_TOPICS", {"real_one": ["general"]}, raising=False)

    unregistered = sorted(m for m in modules if not bh._is_registered_module(m))
    assert unregistered == ["autonomous.tasks", "learning.knowledge_spider"]
    assert "real_one" not in unregistered


def test_get_stats_publishes_the_field():
    """The field must exist on the real payload shape, spelled as callers expect."""
    src = pathlib.Path("aria_service/intel/brain_hook.py").read_text(
        encoding="utf-8", errors="replace"
    )
    assert '"unregistered_modules": unregistered' in src
    assert '"unregistered_count": len(unregistered)' in src
