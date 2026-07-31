"""R-F3585 — a register that does not own a host was unreachable.

LIVE (dd_7d9116dc44fd, Wilson James Limited): the run captured
`www.gov.uk/armed-forces-covenant-businesses/wilson-james-ltd` into press coverage —
a genuine Armed Forces Covenant signatory listing. It was never promoted, because
`_POSITIVE_REGISTERS` is keyed by HOST and the Covenant's signatory list is a PATH on
gov.uk. A credential the DD already held, unreachable.

Keying bare `gov.uk` would make every government page a credential — precisely the
domain-match-alone error R-F3093 removed from the adverse filter. So the key is
HOST + PATH PREFIX, and the path must reach an ENTITY page: the register's own index
is the register existing, not a listing OF the subject.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_orchestrator as dd

_T = {"wilson", "james"}


def _n(url, title="Wilson James Ltd - GOV.UK"):
    return len(dd.positive_register_findings(
        [{"url": url, "title": title, "snippet": ""}], _T, as_of="2026-07-31"))


def test_the_live_covenant_listing_is_promoted():
    """PROVE RED: this was captured and silently dropped."""
    assert _n("https://www.gov.uk/armed-forces-covenant-businesses/wilson-james-ltd") == 1


def test_an_unrelated_gov_uk_page_is_not_a_credential():
    """THE RISK THIS GUARDS. Keying the department would credit every page it serves."""
    assert _n("https://www.gov.uk/government/news/some-announcement") == 0
    assert _n("https://www.gov.uk/") == 0


@pytest.mark.parametrize("url", [
    "https://www.gov.uk/armed-forces-covenant-businesses",
    "https://www.gov.uk/armed-forces-covenant-businesses/",
])
def test_the_register_index_is_not_a_listing_of_the_subject(url):
    """An index page is the register EXISTING. Crediting it manufactures a credential
    from the register's mere existence — and the title anchor cannot catch it, because
    a search result for the index is still titled with the subject's name."""
    assert _n(url) == 0


def test_the_path_prefix_must_end_on_a_boundary():
    """`-businesses-other` is a different path, not a sub-path."""
    assert _n("https://www.gov.uk/armed-forces-covenant-businesses-other/wilson-james") == 0


def test_a_listing_for_another_company_is_still_rejected():
    """The title anchor keeps working through the new matching path."""
    assert _n("https://www.gov.uk/armed-forces-covenant-businesses/babcock",
              "Babcock International listing") == 0


def test_host_keyed_registers_are_unaffected():
    """The SIA register owns its host and must keep matching as before."""
    assert _n("https://www.services.sia.homeoffice.gov.uk/Pages/acs.aspx",
              "Wilson James Limited - Approved Contractor") == 1


def test_the_sweep_queries_the_path_keyed_register_too():
    """A key the sweep cannot ask about is only half-wired."""
    keys = [k for k in dd._POSITIVE_REGISTERS if k not in dd._NOT_A_CREDENTIAL]
    assert "gov.uk/armed-forces-covenant-businesses" in keys, (
        "the path-keyed register is not in the sweep's query set"
    )
