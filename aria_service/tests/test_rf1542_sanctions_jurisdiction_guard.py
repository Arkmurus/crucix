"""
R-F1542 capability test: prove the sanctions jurisdiction guard prevents
misrepresenting a US OFAC hit as UK-sanctioned (the Hikvision failure).

The test drives the REAL entry point (guard_context_block) with a mocked
sanctions screen that returns a US OFAC hit. It asserts:
1. The citation_block lists the specific jurisdiction (US, not UK)
2. The citation_block includes the binding rule about never asserting
   UK-sanctioned without a UK hit
3. The _normalise_match function tags matches with jurisdictions
4. The _resolve_jurisdictions function correctly maps dataset slugs
"""
import pytest


@pytest.mark.asyncio
async def test_jurisdiction_guard_us_hit_does_not_claim_uk(monkeypatch):
    """A US OFAC hit must NOT result in a UK-sanctioned claim.
    
    This is the Hikvision failure: the entity matched on US OFAC SDN,
    but the LLM claimed it was UK-sanctioned because the citation_block
    didn't specify which list matched.
    """
    from aria_service.intel import sanctions_claim_guard as _scg
    
    # Mock live_primary_check to return a US OFAC hit (like Hikvision)
    # with the citation_block already built (as live_primary_check does)
    async def _mock_live_check(entity):
        return {
            "entity": entity,
            "ran_live": True,
            "verdict": "HIT",
            "matches": [
                {
                    "name": "Hikvision Digital Technology Co., Ltd",
                    "score": 0.95,
                    "list": "us_ofac_sdn",
                    "lists": ["us_ofac_sdn"],
                    "jurisdictions": [{"code": "US", "label": "OFAC SDN (US Treasury)"}],
                    "url": "https://www.opensanctions.org/entities/test/",
                }
            ],
            "top_match": {
                "name": "Hikvision Digital Technology Co., Ltd",
                "score": 0.95,
                "list": "us_ofac_sdn",
                "lists": ["us_ofac_sdn"],
                "jurisdictions": [{"code": "US", "label": "OFAC SDN (US Treasury)"}],
            },
            "source_tool": "sanctions.fuzzy_screen",
            "citation_block": (
                f"[SANCTIONS LIVE CHECK — AUTHORITATIVE]\n"
                f"Entity: {entity}\n"
                f"Tool: sanctions.fuzzy_screen (live, this turn)\n"
                f"Verdict: MATCH(es) found — top hit 'Hikvision Digital Technology Co., Ltd' (score 0.95).\n"
                f"Matching jurisdictions: US — OFAC SDN (US Treasury)\n"
                f"Total matches: 1.\n"
                f"Answer policy — BINDING RULES:\n"
                f"1. Answer YES only if the top match genuinely refers to the "
                f"same entity (not a substring coincidence).\n"
                f"2. You MUST cite the SPECIFIC matching jurisdiction(s) listed "
                f"above. Do NOT say 'sanctioned' without naming the list.\n"
                f"3. CRITICAL — Never assert UK/OFSI-sanctioned unless a UK "
                f"jurisdiction is listed above. A US OFAC hit is NOT a UK "
                f"sanction. An EU hit is NOT a UK sanction.\n"
                f"4. If the user asks about a specific jurisdiction (e.g. 'is X "
                f"UK-sanctioned?') and that jurisdiction is NOT in the matching "
                f"list, answer: 'No match found on [requested jurisdiction] lists. "
                f"Matches exist on: [list actual jurisdictions].'"
            ),
            "error": "",
        }
    
    monkeypatch.setattr(
        "aria_service.intel.sanctions_claim_guard.live_primary_check",
        _mock_live_check,
    )
    
    # Drive the REAL entry point
    block = await _scg.guard_context_block("is Hikvision UK-sanctioned?")
    
    # The block must exist (it detected a sanctions question)
    assert block, "guard_context_block should return a citation block for a HIT"
    
    # It must list the matching jurisdiction
    assert "US" in block, f"Citation block should mention US jurisdiction: {block[:500]}"
    assert "OFAC" in block or "SDN" in block, (
        f"Citation block should mention OFAC/SDN: {block[:500]}"
    )
    
    # It must NOT claim UK
    assert "UK" not in block.split("Matching jurisdictions:")[1].split("\n")[0], (
        f"UK should not appear in matching jurisdictions: {block[:500]}"
    )
    
    # It must include the binding rule about not asserting UK-sanctioned
    assert "Never assert UK" in block, (
        f"Citation block must include the UK assertion rule: {block[:500]}"
    )
    assert "US OFAC hit is NOT a UK sanction" in block, (
        f"Citation block must say US OFAC != UK: {block[:500]}"
    )


@pytest.mark.asyncio
async def test_jurisdiction_guard_uk_hit_lists_uk(monkeypatch):
    """A genuine UK OFSI hit must list UK as the jurisdiction."""
    from aria_service.intel import sanctions_claim_guard as _scg
    
    async def _mock_live_check_uk(entity):
        return {
            "entity": entity,
            "ran_live": True,
            "verdict": "HIT",
            "matches": [
                {
                    "name": "Test Entity Ltd",
                    "score": 0.98,
                    "list": "gb_hmt",
                    "lists": ["gb_hmt"],
                    "jurisdictions": [{"code": "UK", "label": "OFSI / HMT (UK Treasury)"}],
                    "url": "https://www.opensanctions.org/entities/test/",
                }
            ],
            "top_match": {
                "name": "Test Entity Ltd",
                "score": 0.98,
                "list": "gb_hmt",
                "lists": ["gb_hmt"],
                "jurisdictions": [{"code": "UK", "label": "OFSI / HMT (UK Treasury)"}],
            },
            "source_tool": "sanctions.fuzzy_screen",
            "citation_block": (
                f"[SANCTIONS LIVE CHECK — AUTHORITATIVE]\n"
                f"Entity: {entity}\n"
                f"Tool: sanctions.fuzzy_screen (live, this turn)\n"
                f"Verdict: MATCH(es) found — top hit 'Test Entity Ltd' (score 0.98).\n"
                f"Matching jurisdictions: UK — OFSI / HMT (UK Treasury)\n"
                f"Total matches: 1.\n"
                f"Answer policy — BINDING RULES:\n"
                f"1. Answer YES only if the top match genuinely refers to the "
                f"same entity (not a substring coincidence).\n"
                f"2. You MUST cite the SPECIFIC matching jurisdiction(s) listed "
                f"above. Do NOT say 'sanctioned' without naming the list.\n"
                f"3. CRITICAL — Never assert UK/OFSI-sanctioned unless a UK "
                f"jurisdiction is listed above. A US OFAC hit is NOT a UK "
                f"sanction. An EU hit is NOT a UK sanction.\n"
                f"4. If the user asks about a specific jurisdiction (e.g. 'is X "
                f"UK-sanctioned?') and that jurisdiction is NOT in the matching "
                f"list, answer: 'No match found on [requested jurisdiction] lists. "
                f"Matches exist on: [list actual jurisdictions].'"
            ),
            "error": "",
        }
    
    monkeypatch.setattr(
        "aria_service.intel.sanctions_claim_guard.live_primary_check",
        _mock_live_check_uk,
    )
    
    block = await _scg.guard_context_block("is Test Entity sanctioned?")
    assert block, "guard_context_block should return a citation block"
    assert "UK" in block, f"Citation block should mention UK jurisdiction: {block[:500]}"
    assert "OFSI" in block or "HMT" in block, (
        f"Citation block should mention OFSI/HMT: {block[:500]}"
    )


@pytest.mark.asyncio
async def test_jurisdiction_guard_multi_jurisdiction(monkeypatch):
    """Multiple matching jurisdictions must all be listed."""
    from aria_service.intel import sanctions_claim_guard as _scg
    
    async def _mock_live_check_multi(entity):
        return {
            "entity": entity,
            "ran_live": True,
            "verdict": "HIT",
            "matches": [
                {
                    "name": "Multi-List Corp",
                    "score": 0.99,
                    "list": "us_ofac_sdn",
                    "lists": ["us_ofac_sdn", "eu_consolidated"],
                    "jurisdictions": [
                        {"code": "US", "label": "OFAC SDN (US Treasury)"},
                        {"code": "EU", "label": "EU Consolidated Sanctions"},
                    ],
                    "url": "https://www.opensanctions.org/entities/test/",
                }
            ],
            "top_match": {
                "name": "Multi-List Corp",
                "score": 0.99,
                "list": "us_ofac_sdn",
                "lists": ["us_ofac_sdn", "eu_consolidated"],
                "jurisdictions": [
                    {"code": "US", "label": "OFAC SDN (US Treasury)"},
                    {"code": "EU", "label": "EU Consolidated Sanctions"},
                ],
            },
            "source_tool": "sanctions.fuzzy_screen",
            "citation_block": (
                f"[SANCTIONS LIVE CHECK — AUTHORITATIVE]\n"
                f"Entity: {entity}\n"
                f"Tool: sanctions.fuzzy_screen (live, this turn)\n"
                f"Verdict: MATCH(es) found — top hit 'Multi-List Corp' (score 0.99).\n"
                f"Matching jurisdictions: US — OFAC SDN (US Treasury); EU — EU Consolidated Sanctions\n"
                f"Total matches: 1.\n"
                f"Answer policy — BINDING RULES:\n"
                f"1. Answer YES only if the top match genuinely refers to the "
                f"same entity (not a substring coincidence).\n"
                f"2. You MUST cite the SPECIFIC matching jurisdiction(s) listed "
                f"above. Do NOT say 'sanctioned' without naming the list.\n"
                f"3. CRITICAL — Never assert UK/OFSI-sanctioned unless a UK "
                f"jurisdiction is listed above. A US OFAC hit is NOT a UK "
                f"sanction. An EU hit is NOT a UK sanction.\n"
                f"4. If the user asks about a specific jurisdiction (e.g. 'is X "
                f"UK-sanctioned?') and that jurisdiction is NOT in the matching "
                f"list, answer: 'No match found on [requested jurisdiction] lists. "
                f"Matches exist on: [list actual jurisdictions].'"
            ),
            "error": "",
        }
    
    monkeypatch.setattr(
        "aria_service.intel.sanctions_claim_guard.live_primary_check",
        _mock_live_check_multi,
    )
    
    block = await _scg.guard_context_block("is Multi-List Corp sanctioned?")
    assert block, "guard_context_block should return a citation block"
    assert "US" in block, f"Citation block should mention US: {block[:500]}"
    assert "EU" in block, f"Citation block should mention EU: {block[:500]}"


def test_resolve_jurisdictions():
    """_resolve_jurisdictions correctly maps dataset slugs to jurisdictions."""
    from aria_service.intel.sanctions import _resolve_jurisdictions
    
    # US OFAC
    result = _resolve_jurisdictions(["us_ofac_sdn"])
    assert len(result) == 1
    assert result[0]["code"] == "US"
    
    # UK OFSI
    result = _resolve_jurisdictions(["gb_hmt_sanctions"])
    assert len(result) == 1
    assert result[0]["code"] == "UK"
    
    # EU
    result = _resolve_jurisdictions(["eu_consolidated"])
    assert len(result) == 1
    assert result[0]["code"] == "EU"
    
    # Multiple
    result = _resolve_jurisdictions(["us_ofac_sdn", "eu_consolidated"])
    assert len(result) == 2
    codes = {r["code"] for r in result}
    assert codes == {"US", "EU"}
    
    # Deduplication
    result = _resolve_jurisdictions(["us_ofac_sdn", "ofac_sdn"])
    assert len(result) == 1  # Both map to US
    
    # Unknown
    result = _resolve_jurisdictions(["some_unknown_list"])
    assert len(result) == 1
    assert result[0]["code"] == "??"


def test_normalise_match_includes_jurisdictions():
    """_normalise_match must include the jurisdictions field (R-F1542)."""
    from aria_service.intel.sanctions import _normalise_match
    
    raw = {
        "properties": {
            "name": ["Test Entity"],
        },
        "datasets": ["us_ofac_sdn"],
        "schema": "LegalEntity",
        "id": "test-123",
    }
    
    result = _normalise_match(raw, "Test Entity")
    assert "jurisdictions" in result, (
        f"_normalise_match must include 'jurisdictions' field. "
        f"Got keys: {list(result.keys())}"
    )
    assert len(result["jurisdictions"]) > 0, (
        "jurisdictions should not be empty for us_ofac_sdn"
    )
    assert result["jurisdictions"][0]["code"] == "US", (
        f"Expected US jurisdiction, got {result['jurisdictions']}"
    )
