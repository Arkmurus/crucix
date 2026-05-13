"""R-F434 — brandified-hostname sanctions cap.

Live evidence 2026-05-13 21:09 (ARIA WhatsApp chat with operator):
ARIA ran DD on ngast.com (Nebraska Gas Turbine — a clean US firm) and
emitted HARD STOP citing an OFAC SDN hit on "Oscar Noe MEDINA GONZALEZ".
The same conversation also produced a HARD STOP on armesavn.com
(Nebraska ARMES Aviation — also clean) citing SHAZAND PETROCHEMICAL.

Root cause: R-F311 brandifies hostname inputs (ngast.com → ngast)
before sanctions screening. Short brandified stems collide with
unrelated SDN entries via OpenSanctions string similarity, and the
classifier escalates to HARD STOP because the colliding SDN entries
carry topic `sanction`. Token-overlap filtering (R-F277/F351) catches
some cases but not all — the operator's chat showed it was bypassed
twice in one conversation.

R-F434 caps any brandified-hostname-derived match at AMBER (regardless
of topic / score) unless the caller passed legal-name corroboration
via known_aliases. The match remains visible in per_match for operator
review; the orchestrator can re-screen with the verified legal name
from the crawl (R-F312 brandified re-fire path) to recover the full
severity if the hit is real.
"""
from __future__ import annotations


# ───────────────────────── Unit — classify_match severity cap ─────────────────


def test_rf434_unit_brandified_match_capped_at_amber_when_no_corroboration():
    """A match tagged as brandified-hostname-derived with no legal-name
    corroboration must be capped at AMBER even when the topic is `sanction`
    and the score is above the threshold."""
    from aria_service.intel._sanctions_classify import classify_match
    m = {
        "name": "Oscar Noe Medina Gonzalez",
        "score": 0.95,
        "topics": ["sanction"],
        "lists": ["us_ofac_sdn"],
        "_from_brandified_hostname": True,
        "_brandified_stem": "ngast",
        "_has_caller_supplied_aliases": False,
    }
    # query_name="" to bypass token-overlap demotion path so we isolate
    # the brandified-hostname cap. (Token-overlap would also demote this
    # specific candidate to info — see noise-filter test below.)
    sev = classify_match(m, query_name="")
    assert sev == "amber", (
        f"R-F434: brandified-hostname match w/o corroboration must be "
        f"capped at amber, got {sev!r}. This is the ngast.com → "
        f"Oscar Medina HARD STOP false positive the operator hit live."
    )


def test_rf434_unit_brandified_match_with_corroboration_preserves_hard_stop():
    """When the caller supplied a verified legal name as corroboration,
    the brandified-hostname cap does NOT fire. A real HARD STOP on the
    corroborated legal name must still surface as HARD STOP."""
    from aria_service.intel._sanctions_classify import classify_match
    m = {
        "name": "Sanctioned Real Co Ltd",
        "score": 0.95,
        "topics": ["sanction"],
        "lists": ["us_ofac_sdn"],
        "_from_brandified_hostname": True,
        "_brandified_stem": "realco",
        "_has_caller_supplied_aliases": True,  # caller passed legal name
    }
    sev = classify_match(m, query_name="Sanctioned Real Co Ltd")
    assert sev == "hard_stop", (
        f"R-F434: corroboration must preserve hard_stop on real sanctions, "
        f"got {sev!r}. Otherwise the cap masks true positives once the "
        f"orchestrator re-screens with the legal name."
    )


def test_rf434_unit_non_brandified_match_unaffected():
    """Matches that did NOT originate from a brandified hostname must
    flow through unchanged — the cap is targeted at the false-positive
    class, not a general HARD STOP suppression."""
    from aria_service.intel._sanctions_classify import classify_match
    m = {
        "name": "Vladimir Vladimirovich Putin",
        "score": 1.00,
        "topics": ["sanction"],
        "lists": ["us_ofac_sdn"],
        # No brandified flags — normal direct-name DD path.
    }
    sev = classify_match(m, query_name="Vladimir Putin")
    assert sev == "hard_stop"


def test_rf434_unit_brandified_amber_topic_stays_amber():
    """A brandified match whose topic is already AMBER (e.g. role.pep)
    is unaffected by the cap — the cap is a ceiling, not a floor."""
    from aria_service.intel._sanctions_classify import classify_match
    m = {
        "name": "Some PEP",
        "score": 0.95,
        "topics": ["role.pep"],
        "lists": ["us_pep_database"],
        "_from_brandified_hostname": True,
        "_has_caller_supplied_aliases": False,
    }
    # query_name="" to bypass token-overlap demotion (single short token
    # would otherwise reduce to info under R-F351).
    sev = classify_match(m, query_name="")
    assert sev == "amber"


# ───────────────────────── Unit — classify_matches transparency ───────────────


def test_rf434_per_match_flags_hostname_capped_entries():
    """The classify_matches per_match output must mark which entries were
    hostname-capped so the operator can see why severity was lowered."""
    from aria_service.intel._sanctions_classify import classify_matches
    matches = [
        {
            "name": "Oscar Noe Medina Gonzalez",
            "score": 0.95,
            "topics": ["sanction"],
            "lists": ["us_ofac_sdn"],
            "_from_brandified_hostname": True,
            "_brandified_stem": "ngast",
            "_has_caller_supplied_aliases": False,
        },
    ]
    out = classify_matches(matches, query_name="ngast")
    assert out["per_match"], "expected per_match entry"
    pm = out["per_match"][0]
    assert pm["brandified_hostname_capped"] is True
    assert pm["brandified_stem"] == "ngast"
    # Final severity must reflect the cap (not hard_stop)
    assert pm["severity"] in ("info", "amber"), pm


def test_rf434_per_match_clean_when_match_not_brandified():
    """Non-brandified matches must NOT have the brandified_hostname_capped
    flag set to True."""
    from aria_service.intel._sanctions_classify import classify_matches
    matches = [
        {
            "name": "Vladimir Vladimirovich Putin",
            "score": 1.00,
            "topics": ["sanction"],
            "lists": ["us_ofac_sdn"],
        },
    ]
    out = classify_matches(matches, query_name="Vladimir Putin")
    pm = out["per_match"][0]
    assert pm["brandified_hostname_capped"] is False


# ───────────────────────── Capability — end-to-end via screen_with_aliases ───


def test_rf434_capability_screen_with_aliases_tags_brandified_matches(monkeypatch):
    """screen_with_aliases on a hostname input must tag returned matches
    with the brandified-hostname origin so the classifier cap fires
    downstream. Mirrors the live ngast.com false-positive surface."""
    import asyncio
    from aria_service.intel import sanctions as _sanc

    # Mock fuzzy_screen to return a fake OFAC SDN-topic match — the
    # exact shape OpenSanctions would emit. Returns the same fake hit
    # for every screened target so we can verify origin tagging on
    # each. We don't go through OpenSanctions to keep the test offline.
    async def _fake_fuzzy_screen(name, *, threshold=0.78):
        return {
            "name": name,
            "matches": [
                {
                    "name": "Oscar Noe Medina Gonzalez",
                    "score": 0.95,
                    "topics": ["sanction"],
                    "lists": ["us_ofac_sdn"],
                    "list": "us_ofac_sdn",
                }
            ],
            "top_score": 0.95,
            "blocked": True,
        }

    monkeypatch.setattr(_sanc, "fuzzy_screen", _fake_fuzzy_screen)

    # Run the screen on a raw hostname — caller did NOT pass any legal-
    # name alias (the operator-typed scenario).
    result = asyncio.run(_sanc.screen_with_aliases("ngast.com"))

    assert result.get("from_brandified_hostname") is True
    assert result.get("brandified_stem")  # something non-empty
    # R-F444: new canonical key is `has_caller_supplied_aliases`;
    # `has_legal_name_corroboration` is preserved as a deprecated alias.
    assert result.get("has_caller_supplied_aliases") is False
    assert result.get("has_legal_name_corroboration") is False  # deprecated alias still present
    assert result["matches"], "expected at least one match"
    for m in result["matches"]:
        assert m.get("_from_brandified_hostname") is True, (
            f"R-F434: screen_with_aliases must tag brandified-derived "
            f"matches so classifier cap fires. Got match without tag: {m}"
        )
        assert m.get("_has_caller_supplied_aliases") is False
        # R-F444 backward-compat alias on per-match too
        assert m.get("_has_legal_name_corroboration") is False


def test_rf434_capability_classifier_caps_ngast_match_to_amber(monkeypatch):
    """End-to-end: screen_with_aliases("ngast.com") + classify_matches
    must yield worst_severity != "hard_stop". Reproduces the operator's
    live ARIA chat failure where ngast.com produced a HARD STOP."""
    import asyncio
    from aria_service.intel import sanctions as _sanc
    from aria_service.intel._sanctions_classify import classify_matches

    async def _fake_fuzzy_screen(name, *, threshold=0.78):
        return {
            "name": name,
            "matches": [
                {
                    "name": "Oscar Noe Medina Gonzalez",
                    "score": 0.95,
                    "topics": ["sanction"],
                    "lists": ["us_ofac_sdn"],
                    "list": "us_ofac_sdn",
                }
            ],
            "top_score": 0.95,
            "blocked": True,
        }
    monkeypatch.setattr(_sanc, "fuzzy_screen", _fake_fuzzy_screen)

    result = asyncio.run(_sanc.screen_with_aliases("ngast.com"))
    classified = classify_matches(result["matches"], query_name="ngast.com")

    assert classified["worst_severity"] != "hard_stop", (
        f"R-F434 capability regression: ngast.com produced HARD STOP — "
        f"this is exactly the false positive the operator hit in chat "
        f"2026-05-13 21:09. Classifier output: {classified}"
    )


def test_rf434_capability_classifier_caps_armesavn_match_to_amber(monkeypatch):
    """Same regression check using the second false positive from the
    same conversation: armesavn.com (8-char stem) → SHAZAND PETROCHEMICAL.
    The 8-char stem confirms the cap is origin-based, not length-based."""
    import asyncio
    from aria_service.intel import sanctions as _sanc
    from aria_service.intel._sanctions_classify import classify_matches

    async def _fake_fuzzy_screen(name, *, threshold=0.78):
        return {
            "name": name,
            "matches": [
                {
                    "name": "Shazand Petrochemical Company",
                    "score": 0.88,
                    "topics": ["sanction"],
                    "lists": ["us_ofac_sdn"],
                    "list": "us_ofac_sdn",
                }
            ],
            "top_score": 0.88,
            "blocked": True,
        }
    monkeypatch.setattr(_sanc, "fuzzy_screen", _fake_fuzzy_screen)

    result = asyncio.run(_sanc.screen_with_aliases("armesavn.com"))
    classified = classify_matches(result["matches"], query_name="armesavn.com")

    assert classified["worst_severity"] != "hard_stop", (
        f"R-F434: armesavn.com produced HARD STOP — second false-positive "
        f"surface from the same chat. Classifier output: {classified}"
    )


def test_rf434_capability_corroboration_restores_hard_stop(monkeypatch):
    """When the orchestrator re-screens with a verified legal name (R-F312
    brandified re-fire path), a REAL hard-stop topic match must still
    fire. The cap must not mask true positives once corroboration is
    available."""
    import asyncio
    from aria_service.intel import sanctions as _sanc
    from aria_service.intel._sanctions_classify import classify_matches

    async def _fake_fuzzy_screen(name, *, threshold=0.78):
        return {
            "name": name,
            "matches": [
                {
                    "name": "Sanctioned Real Co Ltd",
                    "score": 0.97,
                    "topics": ["sanction"],
                    "lists": ["us_ofac_sdn"],
                    "list": "us_ofac_sdn",
                }
            ],
            "top_score": 0.97,
            "blocked": True,
        }
    monkeypatch.setattr(_sanc, "fuzzy_screen", _fake_fuzzy_screen)

    # Caller passed legal-name corroboration as known_aliases.
    result = asyncio.run(_sanc.screen_with_aliases(
        "realco.com",
        known_aliases=["Sanctioned Real Co Ltd"],
    ))
    # R-F444 canonical key + deprecated alias both True
    assert result["has_caller_supplied_aliases"] is True
    assert result["has_legal_name_corroboration"] is True  # deprecated alias
    classified = classify_matches(
        result["matches"],
        query_name="Sanctioned Real Co Ltd",
    )
    assert classified["worst_severity"] == "hard_stop", (
        f"R-F434: corroboration must preserve true positives — the cap "
        f"is targeted at uncorroborated hostname-only screens. Got: "
        f"{classified}"
    )
