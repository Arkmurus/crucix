"""R-F2286 — citation-grounding evidence set must span ALL layers, not press only.

R-F2282 wired source_verifier but built the fetched-evidence set from
`digital.press_coverage[].url` ONLY. So a report whose findings cite a URL ARIA
genuinely consulted via a NON-press source (registry, cert-transparency, GLEIF,
RAG) scored citation_grounding_rate ~0 / "ungrounded" — a FALSE NEGATIVE (proven
live on a QinetiQ DD: crt.sh was cited AND consulted, yet reported ungrounded).
That false "ungrounded" is itself a Phase-A honesty defect.

R-F2286 builds the fetched set from every source/evidence field across the whole
report (via as_dict walk: finding `source`/`sources`, evidence `url`/`snippet`/
`link`), with finding `detail` as the CHECKED prose. These tests prove a citation
grounded by a non-press consulted source is now counted.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel.dd_schema import ARKDDReport, Finding, Evidence

_GLEIF = "https://api.gleif.org/records/213800S8OBDOZMCMUW34"


class TestGroundingSpansAllLayers:
    @pytest.mark.asyncio
    async def test_citation_grounded_by_nonpress_source(self):
        r = ARKDDReport()
        # NO press coverage at all — proves we no longer rely on press_coverage.
        # A compliance finding's SOURCE is a consulted URL (registry/GLEIF)…
        r.compliance.findings = [
            Finding(severity="info", title="registry",
                    detail="Legal entity confirmed in GLEIF.", source=_GLEIF),
        ]
        # …and an identity finding's DETAIL cites that same consulted URL.
        r.identity.findings = [
            Finding(severity="info", title="claim",
                    detail=f"Active per {_GLEIF} — status issued.", source="analysis"),
        ]
        await ddo._run_verification({}, r)
        # ── R-F4225 / C-205 — this used to assert the flag was True, and that
        # assertion had been failing continuously. The CODE is right.
        #
        # When R-F2286 was written, source_verifier running was what set
        # `independent_source_verification_run`. R-F2413 then separated two
        # different claims and R-F2671 gated the flag behind an operator flip:
        #
        #   citation grounding      = did ARIA actually FETCH the URLs it cites?
        #   independent verification = did ARIA RE-FETCH external sources to
        #                              re-confirm each claim's truth?
        #
        # Only the first runs by default. `independent_verify_mode()` documents
        # the gate — "'off' (default) — do not re-fetch; ...stays False" — and
        # R-F2413's rule is that "the flag must be EARNED, never flipped blind".
        #
        # DO NOT "fix" a failure here by making the flag True when source_verifier
        # runs. That is precisely the overclaim R-F2413 removed: it would tell a
        # customer their claims were independently re-verified when only the
        # citations were checked for grounding. Flip it by setting
        # ARIA_DD_INDEPENDENT_VERIFY=enforce, deliberately, or not at all.
        assert r.verification.independent_source_verification_run is False, (
            "the earned-flag guarantee has been weakened — see R-F2413/R-F2671")
        # THE ACTUAL SUBJECT OF R-F2286: grounding spans ALL layers, not press
        # only. There is no press_coverage on this report at all, and the cited
        # URL is reachable only through a compliance finding's `source`.
        # Under R-F2282 (press-only) this was 0 → ungrounded. Now grounded.
        assert r.verification.citations_checked == 1
        assert r.verification.citations_grounded == 1
        assert r.verification.citation_grounding_rate == 1.0

    @pytest.mark.asyncio
    async def test_truly_uncited_url_still_flagged(self):
        # Honest the OTHER way: a prose URL that ARIA never consulted stays ungrounded.
        r = ARKDDReport()
        r.digital.press_coverage = [Evidence(source="Reuters", url="https://reuters.com/a")]
        r.identity.findings = [
            Finding(severity="amber", title="claim",
                    detail="See https://not-consulted.example/z for the allegation.",
                    source="analysis"),
        ]
        await ddo._run_verification({}, r)
        assert r.verification.citations_checked == 1
        assert r.verification.citations_grounded == 0
        assert r.verification.citation_grounding_rate == 0.0
