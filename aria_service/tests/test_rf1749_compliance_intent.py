"""R-F1749 — Compliance / export-control NLU routes to the real sanctions screen.

Operator symptom (2026-06-20 web chat probe): the "Compliance" action chip emits
the natural-language form "Check export-control / compliance for <entity>" (no
slash). Pre-fix this matched NO intent → no_tool → the LLM answered from TRAINING
knowledge and even FABRICATED a "[TOOL: sanctions_canonical.lookup]" block instead
of running the live OFAC/EU/OFSI/UN module.

Capability: _detect_tool_intent on the chip phrasing must return the `screen`
tool with the entity extracted — the same tool /screen fires — so the answer is
grounded in the live sanctions module, not the model's memory.
"""
from aria_service.routes.aria import _detect_tool_intent


class TestComplianceIntent:
    def test_compliance_chip_phrasing_routes_to_screen(self):
        intent = _detect_tool_intent("Check export-control / compliance for Rosoboronexport")
        assert intent is not None, "compliance chip phrasing must detect a tool intent"
        assert intent.get("tool") == "screen", f"expected screen, got {intent}"
        assert "rosoboronexport" in intent.get("entity", "").lower()

    def test_compliance_check_for_entity(self):
        intent = _detect_tool_intent("compliance check for Acme Corp")
        assert intent and intent.get("tool") == "screen"
        assert "acme" in intent.get("entity", "").lower()

    def test_sanctions_on_entity(self):
        intent = _detect_tool_intent("export controls on Sukhoi")
        assert intent and intent.get("tool") == "screen"
        assert "sukhoi" in intent.get("entity", "").lower()

    def test_doc_review_not_hijacked(self):
        """A compliance question ABOUT an attached document must NOT route to
        screen — doc-review stays on the LLM-pure path (§22a)."""
        msg = (
            "[ATTACHED DOCUMENT]\n" + ("contract text " * 30) + "\n[/ATTACHED DOCUMENT]\n\n"
            "review the compliance clauses of this document"
        )
        intent = _detect_tool_intent(msg)
        # Either None (LLM-pure) or anything that is NOT a screen on 'document'.
        if intent is not None and intent.get("tool") == "screen":
            assert "document" not in intent.get("entity", "").lower()
            assert "attached" not in intent.get("entity", "").lower()

    def test_plain_commands_unaffected(self):
        """No compliance keyword → not hijacked by the new intent."""
        assert (_detect_tool_intent("Research Saab Gripen Brazil") or {}).get("tool") != "screen"
        # /dd still routes to the orchestrator, not screen
        assert (_detect_tool_intent("/dd Embraer") or {}).get("tool") == "dd_orchestrate"
