"""R-F603 — TOOL INVENTORY in aria_engine system prompt.

Past incident 2026-05-16: ARIA repeatedly claimed "no UK OFSI access"
while fcdo_sanctions.lookup() was actively fetching the OFSI ConList.xml
in fly logs at 17:42:57. Similarly claimed missing Saudi/Panama/Bulgaria
/Turkey/India registry adapters when registry_adapters._SUPPORTED_
JURISDICTIONS lists all 23. Claimed no outbound email when R-F597
email_outbound module is in integrations/.

Root cause: the system prompt enumerates intelligence LAYERS but did
not enumerate the actual TOOL INVENTORY. The LLM had no in-context
evidence to contradict its own training-prior belief that tools are
missing.

R-F603 adds an explicit "YOUR TOOL INVENTORY" section listing
callable modules so capability questions return real answers.

This file pins:
  - section present in the constitution
  - each Tier-1a sanctions source explicitly named
  - all 23 registry jurisdiction codes named
  - email_outbound module named
  - self_introspect path named
  - policy line: tool failure ≠ capability missing
"""
from __future__ import annotations

import pathlib

from ._source_probe import repo_path


def _prompt_src() -> str:
    return pathlib.Path(
        repo_path("aria_service/aria_engine.py")
    ).read_text(encoding="utf-8", errors="ignore")


# ══════════════════════════════════════════════════════════════════
# PART A — section is present
# ══════════════════════════════════════════════════════════════════

def test_rf603_tool_inventory_section_exists():
    src = _prompt_src()
    assert "YOUR TOOL INVENTORY" in src, (
        "R-F603 regression: TOOL INVENTORY section missing from prompt"
    )


def test_rf603_section_lives_inside_aria_system_prompt():
    """Must be inside the ARIA_SYSTEM_PROMPT string, not just somewhere
    in the file."""
    src = _prompt_src()
    prompt_start = src.find('ARIA_SYSTEM_PROMPT = """')
    prompt_end = src.find('"""', prompt_start + 30)
    assert prompt_start > 0
    assert prompt_end > prompt_start
    block = src[prompt_start:prompt_end]
    assert "YOUR TOOL INVENTORY" in block, (
        "R-F603: section is outside the ARIA_SYSTEM_PROMPT string"
    )


# ══════════════════════════════════════════════════════════════════
# PART B — sanctions sources enumerated
# ══════════════════════════════════════════════════════════════════

def test_rf603_uk_ofsi_explicitly_named():
    """The hallucination that triggered this fix was 'no UK OFSI access'.
    The fix MUST name fcdo_sanctions explicitly."""
    src = _prompt_src()
    assert "fcdo_sanctions" in src
    assert "OFSI" in src


def test_rf603_all_major_sanctions_sources_named():
    """OFAC SDN (US), EU Consolidated, UN SC, World Bank Debarred."""
    src = _prompt_src()
    for kw in ("ofac_sdn", "eu_consolidated", "un_sc_sanctions",
              "worldbank_debarred"):
        assert kw in src, f"R-F603: sanctions source {kw!r} missing from prompt"


# ══════════════════════════════════════════════════════════════════
# PART C — registry adapters (23 jurisdictions named)
# ══════════════════════════════════════════════════════════════════

def test_rf603_registry_adapter_module_named():
    src = _prompt_src()
    assert "registry_adapters" in src


def test_rf603_all_23_jurisdiction_codes_listed():
    """Every supported jurisdiction must be in the prompt — so the
    LLM has token evidence each one exists."""
    src = _prompt_src()
    # Pull the truth from the actual code to avoid drift
    from aria_service.intel.registry_adapters import _SUPPORTED_JURISDICTIONS
    missing = []
    for iso in _SUPPORTED_JURISDICTIONS:
        # Allow flexible formatting — just need the code to appear
        if iso not in src:
            missing.append(iso)
    assert not missing, (
        f"R-F603: registry jurisdictions missing from prompt: {missing}"
    )


def test_rf603_panama_and_bulgaria_specifically_named():
    """Sanity-check the two added today (R-F598)."""
    src = _prompt_src()
    assert "Panama" in src
    assert "Bulgaria" in src


# ══════════════════════════════════════════════════════════════════
# PART D — email + self-instrumentation named
# ══════════════════════════════════════════════════════════════════

def test_rf603_email_outbound_explicitly_named():
    """ARIA claimed 'no outbound email' on 2026-05-16. R-F597 exists."""
    src = _prompt_src()
    assert "email_outbound" in src or "Email SMTP OUT" in src
    assert "R-F597" in src


def test_rf603_self_introspect_path_named():
    """The /health/perf endpoint must be named so the LLM knows it
    exists as a callable tool."""
    src = _prompt_src()
    assert "/api/aria/health/perf" in src or "/health/perf" in src
    assert "self_introspect" in src


# ══════════════════════════════════════════════════════════════════
# PART E — policy guardrail
# ══════════════════════════════════════════════════════════════════

def test_rf603_policy_distinguishes_failure_from_missing():
    """Critical guardrail: a tool call failing does NOT mean the
    capability is missing. This sentence prevents the 'I cannot query
    OFSI' regression."""
    src = _prompt_src()
    assert "FORBIDDEN denials" in src or "FORBIDDEN" in src
    assert ("never extrapolate" in src.lower()
            or "the capability exists; the call failed" in src.lower()
            or "do not make that mistake" in src.lower())


def test_rf603_section_cites_past_incident_anchor():
    """Past-incident anchor must appear so future contributors see WHY."""
    src = _prompt_src()
    section_idx = src.find("YOUR TOOL INVENTORY")
    next_section = src.find("\n\nKNOWLEDGE-FIRST", section_idx)
    block = src[section_idx:next_section if next_section > 0 else section_idx + 6000]
    assert "2026-05-16" in block, (
        "R-F603: tool inventory must cite the past-incident anchor"
    )
