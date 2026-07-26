"""R-F3178 — scoped invite links.

This is the module's first unauthenticated surface, pointing at its most
sensitive data. The tests are written around what a link must NOT be able to
do, because a link will be forwarded, screenshotted, and sit in inboxes for
years. Assume it leaks.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from aria_service.vetting.invites import (
    DEFAULT_TTL_DAYS, MAX_TTL_DAYS, Invite, InviteError, InviteKind,
    applicant_context, mint, referee_context, token_hash,
)
from aria_service.vetting.models import CareerEntry, CareerEntryType, VettingCase
from aria_service.vetting.store import VettingCaseStore

NOW = datetime(2026, 7, 26, tzinfo=UTC)
TENANT = "tenant-a"


def _case():
    return VettingCase(
        tenant_id=TENANT, case_id="C1", applicant_name="Ada Lovelace",
        date_of_birth=date(1990, 1, 1), employment_start=date(2026, 6, 1),
        inputs={"convictions_declared": True},
        career=[CareerEntry(entry_id="e1", entry_type=CareerEntryType.EMPLOYMENT,
                            start=date(2021, 6, 1), end=date(2023, 1, 1),
                            organisation="Alpha Ltd")],
    )


def _mint(kind=InviteKind.APPLICANT, **kw):
    return mint(tenant_id=TENANT, case_id="C1", kind=kind, now=NOW, **kw)


# ── the token itself ──────────────────────────────────────────────────────

def test_plaintext_token_is_never_stored():
    invite, token = _mint()
    assert token not in invite.token_hash
    assert invite.token_hash == token_hash(token)
    assert len(invite.token_hash) == 64          # sha256 hex


def test_tokens_are_unique_and_high_entropy():
    tokens = {_mint()[1] for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) > 40 for t in tokens)


def test_the_employer_facing_record_carries_no_token():
    invite, token = _mint()
    body = invite.as_dict()
    assert "token" not in body and "token_hash" not in body
    assert token not in str(body)


def test_token_prefix_identifies_kind_without_identifying_the_person():
    _, applicant = _mint(InviteKind.APPLICANT)
    _, referee = _mint(InviteKind.REFEREE, entry_id="e1")
    assert applicant.startswith("vpa_") and referee.startswith("vpr_")
    for t in (applicant, referee):
        assert "C1" not in t and "Ada" not in t and TENANT not in t


# ── scope ─────────────────────────────────────────────────────────────────

def test_a_referee_invite_must_name_the_period_it_covers():
    """An unscoped referee link would expose the whole file."""
    with pytest.raises(InviteError, match="career entry"):
        _mint(InviteKind.REFEREE)


def test_referee_context_is_built_by_allowlist_not_by_redaction():
    """A redaction-by-subtraction leaks whatever field is added next — and on a
    vetting case the next field is likely to be a sensitive one."""
    ctx = referee_context(_case(), "e1")
    assert set(ctx) == {"applicant_name", "organisation", "period_from",
                        "period_to", "asked_to_confirm", "note"}


def test_a_referee_learns_nothing_about_convictions_or_findings():
    ctx = referee_context(_case(), "e1")
    blob = str(ctx).lower()
    for leak in ("conviction", "criminal", "finding", "blocker", "verdict",
                 "date_of_birth", "1990", "financial", "bankrupt"):
        assert leak not in blob, f"referee context leaked {leak!r}"


def test_referee_context_refuses_a_period_no_longer_on_the_case():
    with pytest.raises(InviteError):
        referee_context(_case(), "does-not-exist")


def test_applicant_context_does_not_expose_the_employers_assessment():
    ctx = applicant_context(_case())
    blob = str(ctx).lower()
    for leak in ("finding", "blocker", "verdict", "status", "conviction"):
        assert leak not in blob


# ── lifetime ──────────────────────────────────────────────────────────────

def test_invites_expire():
    invite, _ = _mint(ttl_days=1)
    assert invite.is_usable(NOW) is True
    assert invite.is_usable(NOW + timedelta(days=2)) is False
    assert invite.is_expired(NOW + timedelta(days=2)) is True


def test_ttl_is_clamped_so_a_link_cannot_be_made_immortal():
    long_lived, _ = _mint(ttl_days=10_000)
    assert long_lived.is_expired(NOW + timedelta(days=MAX_TTL_DAYS + 1))
    short, _ = _mint(ttl_days=0)
    assert short.is_usable(NOW)          # clamped up to at least a day


def test_default_ttl_is_two_weeks():
    invite, _ = _mint()
    expected = (NOW + timedelta(days=DEFAULT_TTL_DAYS)).isoformat()
    assert invite.expires_at == expected


def test_a_revoked_invite_is_unusable_immediately():
    invite, _ = _mint()
    revoked = Invite(**{**invite.__dict__, "revoked_at": NOW.isoformat()})
    assert revoked.is_usable(NOW) is False


# ── storage ───────────────────────────────────────────────────────────────

@pytest.fixture()
def store(tmp_path):
    return VettingCaseStore(db_path=tmp_path / "inv.db")


def test_a_token_resolves_only_via_its_hash(store):
    invite, token = _mint()
    store.save_invite(invite)
    assert store.get_invite_by_token_hash(token_hash(token)).invite_id == invite.invite_id
    # The raw token is not a key, and neither is the id.
    assert store.get_invite_by_token_hash(token) is None
    assert store.get_invite_by_token_hash(invite.invite_id) is None
    assert store.get_invite_by_token_hash("") is None


def test_revocation_is_scoped_to_the_owning_tenant(store):
    invite, _ = _mint()
    store.save_invite(invite)
    assert store.revoke_invite("tenant-b", invite.invite_id, NOW.isoformat()) is False
    assert store.revoke_invite(TENANT, invite.invite_id, NOW.isoformat()) is True
    # Idempotent: a second revoke changes nothing.
    assert store.revoke_invite(TENANT, invite.invite_id, NOW.isoformat()) is False


def test_listing_invites_is_tenant_scoped(store):
    invite, _ = _mint()
    store.save_invite(invite)
    assert len(store.list_invites(TENANT, "C1")) == 1
    assert store.list_invites("tenant-b", "C1") == []
    assert store.list_invites("", "C1") == []


def test_use_is_counted_so_a_shared_link_is_visible(store):
    """A link used far more than expected is the signal that it was forwarded."""
    invite, token = _mint()
    store.save_invite(invite)
    for _ in range(3):
        store.record_invite_use(token_hash(token))
    assert store.get_invite_by_token_hash(token_hash(token)).used_count == 3
