"""R-F1414 — Country sanctions regime lookup capability tests.

Tests that "is [country] under sanctions?" returns a regime answer grounded
in legal instruments (UNSCR numbers, EU regulations, Executive Orders), not
a static table or entity-list hit.

Capability test: "is Iraq under EU/US sanctions?" returns grounded answer
with cited instruments, classifies NOT-comprehensive + arms-embargo-with-gov-
exception + targeted measures, and offers entity-screen handoff.
"""
import pytest

from aria_service.intel.country_sanctions import (
    lookup_country,
    format_regime_answer,
    SanctionsRegime,
)


class TestIraqGoldAnswer:
    """The gold-standard test: Iraq sanctions answer must be correct."""

    def test_iraq_has_un_arms_embargo(self):
        """Iraq has a UN arms embargo with government exception."""
        regimes = lookup_country("Iraq", source="un")
        assert len(regimes) >= 1
        r = regimes[0]
        assert r.regime_type == "arms_embargo"
        assert r.in_force is True
        # Must cite UNSCR 1546
        assert any("1546" in instr for instr in r.instruments)
        # Must have government exception
        assert "Government-of-Iraq" in r.exceptions or "government" in r.exceptions.lower()

    def test_iraq_eu_is_targeted_not_comprehensive(self):
        """EU sanctions on Iraq are targeted, not comprehensive."""
        regimes = lookup_country("Iraq", source="eu")
        assert len(regimes) >= 1
        r = regimes[0]
        assert r.regime_type == "targeted"
        assert r.in_force is True
        # Must cite EU Regulation 1210/2003
        assert any("1210" in instr for instr in r.instruments)

    def test_iraq_us_is_targeted_not_comprehensive(self):
        """US sanctions on Iraq are targeted, not comprehensive."""
        regimes = lookup_country("Iraq", source="us")
        assert len(regimes) >= 1
        r = regimes[0]
        assert r.regime_type == "targeted"
        assert r.in_force is True

    def test_iraq_not_comprehensive(self):
        """Iraq is NOT comprehensively sanctioned."""
        answer = format_regime_answer("Iraq")
        assert answer["found"] is True
        assert answer["has_comprehensive"] is False
        assert answer["has_arms_embargo"] is True
        assert answer["has_targeted"] is True
        assert answer["worst_regime_type"] == "arms_embargo"

    def test_iraq_summary_cites_instruments(self):
        """The Iraq answer summary cites legal instruments."""
        answer = format_regime_answer("Iraq")
        assert "1546" in answer["summary"]
        assert "1210" in answer["summary"]
        assert "Executive Order" in answer["summary"]

    def test_iraq_summary_mentions_gov_exception(self):
        """The Iraq answer mentions the Government-of-Iraq exception."""
        answer = format_regime_answer("Iraq")
        assert "Government" in answer["summary"] or "government" in answer["summary"].lower()

    def test_iraq_offers_entity_screen(self):
        """The Iraq answer offers entity screening handoff."""
        answer = format_regime_answer("Iraq")
        assert answer["entity_screen_offered"] is True

    def test_iraq_has_caveat(self):
        """The Iraq answer includes a regime-change caveat."""
        answer = format_regime_answer("Iraq")
        assert "change" in answer["caveat"].lower()
        assert "verify" in answer["caveat"].lower()


class TestCountryLookup:
    """General country sanctions lookup tests."""

    def test_iran_comprehensive_us(self):
        """US sanctions on Iran are comprehensive."""
        regimes = lookup_country("Iran", source="us")
        assert len(regimes) >= 1
        assert regimes[0].regime_type == "comprehensive"

    def test_iran_targeted_eu(self):
        """EU sanctions on Iran are targeted."""
        regimes = lookup_country("Iran", source="eu")
        assert len(regimes) >= 1
        assert regimes[0].regime_type == "targeted"

    def test_north_korea_comprehensive(self):
        """North Korea has comprehensive sanctions from all sources."""
        for src in ("un", "eu", "us"):
            regimes = lookup_country("North Korea", source=src)
            assert len(regimes) >= 1
            assert regimes[0].regime_type == "comprehensive"

    def test_russia_targeted_not_comprehensive(self):
        """Russia sanctions are targeted/sectoral, not comprehensive."""
        for src in ("eu", "us", "uk"):
            regimes = lookup_country("Russia", source=src)
            assert len(regimes) >= 1
            assert regimes[0].regime_type == "targeted"

    def test_yemen_targeted_not_general(self):
        """Yemen sanctions are targeted (Houthi-specific), not general."""
        regimes = lookup_country("Yemen", source="un")
        assert len(regimes) >= 1
        assert regimes[0].regime_type == "targeted"
        assert "Houthi" in regimes[0].exceptions

    def test_unknown_country_returns_not_found(self):
        """Unknown country returns found=False."""
        answer = format_regime_answer("Atlantis")
        assert answer["found"] is False
        assert answer["entity_screen_offered"] is True

    def test_country_alias_works(self):
        """Country aliases resolve correctly."""
        regimes = lookup_country("DPRK")
        assert len(regimes) >= 1
        assert regimes[0].country.lower() == "north korea"

        regimes2 = lookup_country("Burma")
        assert len(regimes2) >= 1
        assert regimes2[0].country.lower() == "myanmar"

    def test_case_insensitive(self):
        """Country lookup is case-insensitive."""
        for name in ("iraq", "IRAQ", "Iraq", "IrAq"):
            regimes = lookup_country(name)
            assert len(regimes) >= 1

    def test_all_regimes_have_instruments(self):
        """Every regime entry cites at least one legal instrument."""
        from aria_service.intel.country_sanctions import _COUNTRY_REGIMES
        for r in _COUNTRY_REGIMES:
            assert len(r.instruments) >= 1, f"{r.country}/{r.source} has no instruments"

    def test_all_regimes_have_last_reviewed(self):
        """Every regime entry has a last_reviewed date."""
        from aria_service.intel.country_sanctions import _COUNTRY_REGIMES
        for r in _COUNTRY_REGIMES:
            assert r.last_reviewed, f"{r.country}/{r.source} has no last_reviewed"

    def test_all_regimes_have_detail(self):
        """Every regime entry has a detail description."""
        from aria_service.intel.country_sanctions import _COUNTRY_REGIMES
        for r in _COUNTRY_REGIMES:
            assert r.detail, f"{r.country}/{r.source} has no detail"

    def test_no_cross_contaminated_instruments(self):
        """No instrument string appears under two unrelated countries.

        This is a citation-integrity test: a wrong instrument number is worse
        than none. Every instrument must be specific to its country. Genuinely
        shared instruments (e.g. UK SAMLA 2018, which is the enabling legislation
        for ALL UK sanctions) are exempted via an allowlist.
        """
        from aria_service.intel.country_sanctions import _COUNTRY_REGIMES

        # Instruments that are genuinely shared across countries
        _SHARED_INSTRUMENTS = {
            "UK SAMLA 2018",  # enabling legislation for all UK sanctions
        }

        instr_to_countries: dict[str, set[str]] = {}
        for r in _COUNTRY_REGIMES:
            for instr in r.instruments:
                if instr in _SHARED_INSTRUMENTS:
                    continue
                if instr not in instr_to_countries:
                    instr_to_countries[instr] = set()
                instr_to_countries[instr].add(r.country)

        duplicates = {k: v for k, v in instr_to_countries.items() if len(v) > 1}
        assert len(duplicates) == 0, (
            f"Cross-contaminated instruments found: {duplicates}"
        )

    # ── R-F1425: per-country instrument verification ──────────────────────────
    # Each instrument must be an actual sanction ON that country, not adjacent
    # law. The EU Blocking Statute (2018/1100) protects EU firms FROM US
    # sanctions — listing it as an Iran sanction is misleading. This test
    # maintains a known-misattribution allowlist and fails if any instrument
    # on it appears in the regime table.
    _KNOWN_MISATTRIBUTED_INSTRUMENTS: dict[str, str] = {
        # Each entry: instrument → reason it is NOT a sanction on the country
        "EU Regulation 2018/1100": (
            "EU Blocking Statute — protects EU firms FROM US extraterritorial "
            "sanctions (Iran, Cuba, CAATSA). It is NOT a sanction ON Iran."
        ),
    }

    def test_no_known_misattributed_instruments(self):
        """No known-misattributed instrument appears in the regime table.

        R-F1425: duplicate-checking catches cross-contamination but NOT
        wrong-instrument-for-country (2018/1100 was unique to Iran and passed
        the cross-contamination check). This test maintains a curated list of
        instruments that are known to be misattributed as sanctions and fails
        if any appear in the regime table.
        """
        from aria_service.intel.country_sanctions import _COUNTRY_REGIMES

        found: list[tuple[str, str, str]] = []  # (country, source, instrument)
        for r in _COUNTRY_REGIMES:
            for instr in r.instruments:
                if instr in self._KNOWN_MISATTRIBUTED_INSTRUMENTS:
                    found.append((r.country, r.source, instr))

        assert len(found) == 0, (
            f"Known-misattributed instruments found in regime table:\n"
            + "\n".join(
                f"  {c}/{s}: {i} — {self._KNOWN_MISATTRIBUTED_INSTRUMENTS[i]}"
                for c, s, i in found
            )
        )


class TestFormatAnswer:
    """Tests for the formatted answer structure."""

    def test_format_returns_correct_structure(self):
        """format_regime_answer returns the expected dict structure."""
        answer = format_regime_answer("Iraq")
        assert "country" in answer
        assert "found" in answer
        assert "regimes" in answer
        assert "summary" in answer
        assert "worst_regime_type" in answer
        assert "has_comprehensive" in answer
        assert "has_arms_embargo" in answer
        assert "has_targeted" in answer
        assert "entity_screen_offered" in answer
        assert "caveat" in answer

    def test_format_with_source_filter(self):
        """format_regime_answer with source filter returns only that source."""
        answer = format_regime_answer("Iraq", source="us")
        assert answer["found"] is True
        for r in answer["regimes"]:
            assert r["source"] == "us"

    def test_format_unknown_country(self):
        """format_regime_answer for unknown country returns found=False."""
        answer = format_regime_answer("Narnia")
        assert answer["found"] is False
        assert "verify" in answer["summary"].lower()
