"""R-F4046 (C-106) — knowledge-producing modules were routing to `general`.

`brain_hook.absorb()` routes a module's knowledge into the learning tiers by
topic:

    topics = list(_MODULE_TOPICS.get(module, ["general"]))

A module missing from the registry is therefore not broken — its knowledge is
still absorbed — but it lands in an undifferentiated `general` bucket instead of
`compliance` / `sanctions` / `market_intel` / …, which is where the topic-aware
tiers look.

MEASURED 2026-08-16, whole tree:

    registry entries                : 159
    distinct absorb() module names  : 124
    distinct telemetry module names : 481
    unregistered AND using absorb() :  25   <- real routing loss
    unregistered, telemetry-only    : 343   <- topics never consulted

That 481-vs-159 gap is the finding that scopes this fix: `_MODULE_TOPICS` is a
ROUTING TABLE, not an inventory of what exists, and it never could be — C-104
already showed 87.8% of live brain signals come from names outside it
(`redis_store` alone sends 15,481). Registering all 343 telemetry names would be
fabrication: `absorb` is the only caller that reads topics, so for them the
entry would be decoration.

So only the 25 matter, and even among those a WRONG topic is worse than the safe
`general` default — mis-tagged knowledge is retrieved for the wrong questions.
Modules are therefore registered only where the domain is evidenced by existing
precedent in the table itself; the rest stay on `general` and are declared.

THE ANTI-ROT MECHANISM is the point of this file. A hand-maintained list against
a growing tree always rots (§27d says exactly this about the search engine list),
and this one had already drifted to 25. The guard below fails when a NEW
`absorb()` module appears with neither a topic entry nor a deliberate
declaration — forcing the decision at the moment someone actually knows the
domain, rather than silently defaulting.
"""
from __future__ import annotations

import glob
import os
import pathlib
import re

from aria_service.intel.brain_hook import _MODULE_TOPICS


# Modules that call absorb() but deliberately keep the `general` default,
# because their output is infrastructure/self-observation rather than
# domain knowledge, or the domain is genuinely ambiguous. SHRINK-ONLY:
# adding a name here needs a reason, and the right move is usually a topic.
DELIBERATELY_GENERAL = {
    "aria_coder",           # self-coding activity, not domain knowledge
    "bg_supervisor",        # background task supervision
    "boot_diagnostic",      # boot health
    "constitution_test",    # self-test outcomes
    "deploy",               # deploy events
    "machines_deployer",    # infra provisioning
    "paraphrase_guard",     # output guard decisions
    "response_verifier",    # answer verification outcomes
    "rag_store",            # storage layer, topic belongs to the ingested doc
    "collab_bridge",        # agent-to-agent traffic
    "client_learning",      # per-client adaptation, topic varies by client
}


def _absorb_module_names() -> dict[str, str]:
    """Module names passed to absorb()/absorb_silent() across the tree."""
    found: dict[str, str] = {}
    for f in glob.glob("aria_service/**/*.py", recursive=True):
        norm = f.replace(os.sep, "/")
        if "/tests/" in norm:
            continue
        text = pathlib.Path(f).read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(
            r'absorb(?:_silent)?\(\s*\n?\s*module\s*=\s*"([^"]+)"', text
        ):
            found.setdefault(m.group(1), norm)
    return found


def test_the_scan_finds_absorb_callers():
    """Guard the guard: an empty scan would make every assertion below vacuous."""
    names = _absorb_module_names()
    assert len(names) > 50, (
        f"only {len(names)} absorb() call sites found — the scan is broken, and "
        f"a guard whose universe is empty always certifies (§1)"
    )


def test_every_knowledge_module_has_a_topic_decision():
    """A new absorb() module must get a topic, or be declared general on purpose."""
    undeclared = {
        name: path
        for name, path in _absorb_module_names().items()
        if name not in _MODULE_TOPICS and name not in DELIBERATELY_GENERAL
    }
    assert not undeclared, (
        "these modules absorb knowledge but have no topic decision, so it routes "
        "to the undifferentiated 'general' bucket:\n  "
        + "\n  ".join(f"{n}  ({p})" for n, p in sorted(undeclared.items()))
        + "\n\nAdd an entry to brain_hook._MODULE_TOPICS with real topics, or — "
        "if the output is infrastructure rather than domain knowledge — add it "
        "to DELIBERATELY_GENERAL with a reason."
    )


def test_declared_general_modules_are_not_also_registered():
    """The two lists must not disagree about the same module."""
    both = sorted(DELIBERATELY_GENERAL & set(_MODULE_TOPICS))
    assert not both, (
        f"{both} are both registered and declared deliberately-general — remove "
        f"them from DELIBERATELY_GENERAL, the registry entry wins"
    )


def test_registered_topics_use_the_existing_vocabulary():
    """A typo'd topic silently creates a bucket nothing reads."""
    vocabulary = {
        "compliance", "general", "osint", "market_intel", "legal",
        "procurement", "relationships", "geopolitics", "competitor_intel",
        "finance", "sanctions", "technical",
    }
    bad = {
        mod: [t for t in topics if t not in vocabulary]
        for mod, topics in _MODULE_TOPICS.items()
        if isinstance(topics, list) and any(t not in vocabulary for t in topics)
    }
    assert not bad, f"topics outside the known vocabulary: {bad}"


def test_the_sanctions_family_routes_to_sanctions():
    """Precedent-backed: these were the clearest of the 25, and are load-bearing
    for the never-false-clean property."""
    for mod in ("crypto_sanctions", "rca_screening"):
        assert mod in _MODULE_TOPICS, f"{mod} still routes to 'general'"
        assert "sanctions" in _MODULE_TOPICS[mod], (
            f"{mod} must carry the 'sanctions' topic — it is screening output"
        )
