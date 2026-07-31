"""R-F3583 — a namesake PERSON in surname-first form entered Cited sources.

LIVE, in a delivered report (dd_acaee511f0f4, Wilson James Limited, reg 02269560 — a
London security contractor). Its Cited sources listed:

    "Roseburg names Wilson, James to board of directors | Woodworking Network"

A US wood-products company appointing an individual called James Wilson.

WHY THE EXISTING GATES MISSED IT:
  * R-F3221 requires TWO distinctive tokens for aboutness — and both `wilson` and
    `james` are present, so it reads as a strong match.
  * ORDER cannot separate them either: "Wilson, James" runs wilson→james, the SAME
    order as the company. The order test that fixed R-F3576 (FCA) and R-F3579
    (sanctions) does not transfer to this case.

The one discriminator is the COMMA — the surname-first person convention, the same
inversion behind R-F3030's "family cluster" defect.

The rule is deliberately narrow: reject only when the title carries the tokens
EXCLUSIVELY as "tok, tok" and never as the plain phrase. Dropping a genuine hit would
trade a false source for a missing one, and on a DD both are failures.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_orchestrator import (
    _entity_distinctive_tokens as toks,
    _press_hit_is_relevant as relevant,
)

_T = toks("Wilson James Limited")


def test_the_tokens_are_what_we_think():
    assert _T == ["wilson", "james"], _T


# ── the defect ────────────────────────────────────────────────────────────────

def test_the_live_namesake_headline_is_rejected():
    """PROVE RED: this was admitted into a delivered report's Cited sources."""
    assert relevant(
        "Roseburg names Wilson, James to board of directors | Woodworking Network",
        "", "https://www.woodworkingnetwork.com/news/roseburg-names-wilson-james-board",
        _T) is False


def test_the_url_slug_cannot_readmit_it():
    """A slug normalises "Wilson, James" to "wilson-james" and destroys the comma the
    decision rests on — so the URL must not be consulted for this test."""
    assert relevant("Roseburg names Wilson, James to the board", "",
                    "https://example.com/roseburg-names-wilson-james-board", _T) is False


# ── it must not cost genuine coverage ────────────────────────────────────────

@pytest.mark.parametrize("title,url", [
    ("Home - Wilson James", "https://wilsonjames.co.uk/"),
    ("Wilson James Limited - Company Profile - Pomanda",
     "https://pomanda.com/company/02269560/wilson-james-limited"),
    ("Gary Sullivan - Wilson James Limited | LinkedIn",
     "https://www.linkedin.com/in/gary-sullivan-61a6b57/"),
    ("WILSON JAMES GROUP LIMITED overview - GOV.UK",
     "https://find-and-update.company-information.service.gov.uk/company/06527539"),
    ("Wilson James Ltd - GOV.UK",
     "https://www.gov.uk/armed-forces-covenant-businesses/wilson-james-ltd"),
    ("Leadership - Wilson James", "https://wilsonjames.co.uk/leadership"),
])
def test_real_captured_sources_are_still_admitted(title, url):
    """Every one of these was genuinely captured on the live run and is about the
    subject. A filter that drops them trades a false source for a missing one."""
    assert relevant(title, "", url, _T) is True, title


def test_a_title_carrying_BOTH_forms_is_admitted():
    """The rule fires only when the plain phrase is ABSENT."""
    assert relevant("Wilson, James appointed to Wilson James Limited board",
                    "", "https://x.example/a", _T) is True


def test_a_hyphenated_company_name_is_admitted():
    """"Wilson-James" is the company written with a hyphen, not a person."""
    assert relevant("Wilson-James wins contract", "", "https://x.example/b", _T) is True


# ── the rule must not misfire on other entity shapes ─────────────────────────

def test_a_single_token_entity_is_untouched():
    """One distinctive token has no inversion to detect; the rule must not engage."""
    t = toks("Rosoboronexport")
    assert relevant("Rosoboronexport, JSC sanctioned", "", "https://x.example/c", t) is True


def test_an_unrelated_hit_is_still_rejected_by_the_existing_gate():
    """R-F3221's behaviour must be preserved — this fix is additive."""
    assert relevant("Completely unrelated headline", "", "https://x.example/d", _T) is False


def test_a_comma_between_DIFFERENT_tokens_does_not_trigger():
    """A list that happens to contain a comma is not a surname inversion."""
    t = toks("Acme Defence Systems")
    assert relevant("Acme Defence Systems wins award", "", "https://x.example/e", t) is True
