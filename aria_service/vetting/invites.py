"""R-F3178 — scoped invite links for applicants and referees.

This is the module's first UNAUTHENTICATED surface, and it points at the most
sensitive data the platform holds. The design is therefore built around what a
link must NOT be able to do, not around what it enables.

── Threat model ──────────────────────────────────────────────────────────
A link is a bearer credential that will travel by SMS, email and printed QR
code. It will be forwarded, screenshotted, and left in inboxes for years.
Assume it leaks. Everything below follows from that:

* **Stored hashed.** Only SHA-256 of the token is persisted, exactly as API
  keys are. A database read cannot recover a working link.
* **Upload-only.** No invite can READ a case, its findings, its verdict, or
  any other document. The employer's assessment is not the applicant's to see
  through this channel, and it is certainly not a referee's. Art. 15 access
  exists and runs through an identified route, not a forwarded link.
* **Scoped to ONE case, and for a referee to ONE PERIOD.** A referee giving a
  reference for a 2019 job has no business knowing what else is on the file —
  or, critically, that criminal-record data exists on it at all.
* **Expires.** Default 14 days. A screening file outlives its links.
* **Revocable**, and revocation is immediate.
* **Never carries PII in the URL.** The token is opaque; the case id and the
  applicant's name are not in it.

── What a referee is told ────────────────────────────────────────────────
The minimum that lets them answer: who the applicant is, the organisation and
the dates being confirmed. Nothing else. `referee_context()` is the only
function that builds it, so there is one place to audit — and it is built by
allowlist, never by removing fields from a case, because a redaction-by-
subtraction misses whatever field is added next.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum


class InviteKind(str, Enum):
    APPLICANT = "APPLICANT"     # the person being screened, uploading their own evidence
    REFEREE = "REFEREE"         # a nominated third party confirming ONE period


DEFAULT_TTL_DAYS = 14
MAX_TTL_DAYS = 90

# Deliberately not the case id: an opaque prefix tells a support engineer what
# kind of link they are looking at without revealing whose it is.
_PREFIX = {InviteKind.APPLICANT: "vpa", InviteKind.REFEREE: "vpr"}


class InviteError(ValueError):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Invite:
    invite_id: str
    tenant_id: str
    case_id: str
    kind: InviteKind
    token_hash: str
    expires_at: str
    created_at: str
    # Referee-only scope. entry_id names the ONE career period this referee is
    # being asked about; nothing else on the case is in scope.
    entry_id: str = ""
    referee_name: str = ""
    referee_email: str = ""
    revoked_at: str = ""
    used_count: int = 0

    @property
    def revoked(self) -> bool:
        return bool(self.revoked_at)

    def is_expired(self, now: datetime) -> bool:
        return datetime.fromisoformat(self.expires_at) <= now

    def is_usable(self, now: datetime) -> bool:
        return not self.revoked and not self.is_expired(now)

    def as_dict(self) -> dict:
        """Safe to return to the EMPLOYER. Carries no token."""
        return {
            "invite_id": self.invite_id,
            "case_id": self.case_id,
            "kind": self.kind.value,
            "entry_id": self.entry_id,
            "referee_name": self.referee_name,
            "referee_email": self.referee_email,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "revoked": self.revoked,
            "used_count": self.used_count,
        }


def mint(
    *,
    tenant_id: str,
    case_id: str,
    kind: InviteKind,
    now: datetime,
    ttl_days: int = DEFAULT_TTL_DAYS,
    entry_id: str = "",
    referee_name: str = "",
    referee_email: str = "",
) -> tuple[Invite, str]:
    """Create an invite. Returns (record, PLAINTEXT token).

    The plaintext is returned ONCE and never stored. The caller sends it and
    forgets it; there is no path that can re-display it later, which is what
    makes "assume it leaks" survivable — a leak of the database is not a leak
    of the links.
    """
    if not tenant_id or not case_id:
        raise InviteError("tenant and case are required")
    if kind is InviteKind.REFEREE and not entry_id:
        raise InviteError(
            "a referee invite must name the career entry it covers — an "
            "unscoped referee link would expose the whole file")
    ttl = max(1, min(int(ttl_days), MAX_TTL_DAYS))

    secret = secrets.token_urlsafe(32)
    token = f"{_PREFIX[kind]}_{secret}"
    invite = Invite(
        invite_id=f"inv_{secrets.token_hex(8)}",
        tenant_id=tenant_id,
        case_id=case_id,
        kind=kind,
        token_hash=_hash(token),
        expires_at=(now + timedelta(days=ttl)).isoformat(),
        created_at=now.isoformat(),
        entry_id=entry_id,
        referee_name=referee_name.strip(),
        referee_email=referee_email.strip(),
    )
    return invite, token


def token_hash(token: str) -> str:
    """Look-up key for a presented token."""
    return _hash((token or "").strip())


def applicant_context(case) -> dict:
    """What the APPLICANT sees. They know who they are; they do not get the
    employer's assessment."""
    return {
        "case_reference": case.case_id,
        "applicant_name": case.applicant_name,
        "purpose": "pre-employment screening",
        "what_to_upload": (
            "Documents covering your employment, education and any gaps over "
            "the screening period — payslips, P60s, P45s, contracts, or "
            "letters confirming dates."
        ),
    }


def referee_context(case, entry_id: str) -> dict:
    """What a REFEREE sees — built by ALLOWLIST.

    Never by stripping fields off a case: a redaction-by-subtraction silently
    leaks whatever field is added to the model next, and the field most likely
    to be added to a vetting case is a sensitive one.
    """
    entry = next((e for e in case.career if e.entry_id == entry_id), None)
    if entry is None:
        raise InviteError("the referenced period is no longer on this case")
    return {
        "applicant_name": case.applicant_name,
        "organisation": entry.organisation or "",
        "period_from": entry.start.isoformat(),
        "period_to": entry.end.isoformat() if entry.end else "present",
        "asked_to_confirm": (
            "the dates and nature of this person's engagement with your "
            "organisation"
        ),
        # Stated so a referee is never misled about what they are joining.
        "note": (
            "You are being asked only to confirm this engagement. No other "
            "information about the applicant is shared with you."
        ),
    }
