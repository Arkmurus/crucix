"""R-F3188 — the verification request ledger.

This is the right-hand side of the manual Verification Progress Sheet: the
columns headed Code / Request sent / Reply rec. / Audit, and the block of
"Emails and Letters to Applicant" beneath them. The codes are the operator's
own, taken verbatim from the sheet, because a vetting officer moving onto this
system should recognise their own vocabulary rather than learn ours.

── Fed by the invite flow, not parallel to it ────────────────────────────
The important design point. Sharing a referee link IS sending a work reference
request — so minting an invite RECORDS a request, rather than the officer
tracking the same act twice in two places. A ledger you have to remember to
update is the thing the paper sheet already was; duplicating it in software
would move the clerical work rather than remove it.

`invite_id` links the two, so "we asked Alpha Ltd on the 3rd" and "here is the
link they used, and whether they used it" are one fact.

── Chasers are requests too ──────────────────────────────────────────────
`CL` (chaser letter) is one of the operator's own codes, so a chaser is
recorded as a request in its own right with `chases` pointing at the original.
That keeps the audit trail honest: three chasers are three rows, which is what
an auditor asking "what did you actually do to obtain this?" needs to see.

── Overdue is COMPUTED, never stored ─────────────────────────────────────
Whether a request is overdue is a function of (sent_at, as_of, policy), so it
is derived on read like everything else in this engine. A stored "overdue"
flag would be another cached verdict going stale behind a changing file —
exactly the defect R-F3172 had to fix on the assessment cache.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum


class RequestCode(str, Enum):
    """The operator's own codes, verbatim from the Verification Progress Sheet."""

    AR = "AR"      # Accountant's reference
    CL = "CL"      # Chaser letter
    CR = "CR"      # Character reference
    DR = "DR"      # Documentation request
    ER = "ER"      # Education reference
    GR = "GR"      # Gap reference
    SDR = "SDR"    # Statutory declaration request
    TR = "TR"      # Trade reference
    WR = "WR"      # Work reference


CODE_LABELS: dict[RequestCode, str] = {
    RequestCode.AR: "Accountant's reference",
    RequestCode.CL: "Chaser letter",
    RequestCode.CR: "Character reference",
    RequestCode.DR: "Documentation request",
    RequestCode.ER: "Education reference",
    RequestCode.GR: "Gap reference",
    RequestCode.SDR: "Statutory declaration request",
    RequestCode.TR: "Trade reference",
    RequestCode.WR: "Work reference",
}


class RequestStatus(str, Enum):
    SENT = "SENT"
    REPLY_RECEIVED = "REPLY_RECEIVED"
    REFUSED = "REFUSED"            # the referee declined to answer
    UNDELIVERABLE = "UNDELIVERABLE" # bounced / wrong contact
    CANCELLED = "CANCELLED"


# Terminal states: a chaser against one of these would be noise.
_CLOSED = {RequestStatus.REPLY_RECEIVED, RequestStatus.REFUSED,
           RequestStatus.CANCELLED}

# Working default. BS 7858 sets the OVERALL completion clock (12/16 weeks);
# it does not prescribe a chase interval, so this is house policy and is
# overridable per call rather than hardcoded as if it were the standard.
DEFAULT_CHASE_AFTER_DAYS = 10


class RequestError(ValueError):
    pass


@dataclass(frozen=True)
class VerificationRequest:
    request_id: str
    case_id: str
    code: RequestCode
    sent_to: str
    sent_at: str                    # ISO date
    status: RequestStatus = RequestStatus.SENT
    entry_id: str = ""              # the career period this covers, if any
    channel: str = ""               # email | whatsapp | link | post | phone
    invite_id: str = ""             # the link this request was sent as
    replied_at: str = ""
    chases: str = ""                # request_id this chaser follows up
    note: str = ""

    @property
    def is_open(self) -> bool:
        return self.status not in _CLOSED

    def days_outstanding(self, as_of: date) -> int:
        if not self.is_open:
            return 0
        return max(0, (as_of - date.fromisoformat(self.sent_at)).days)

    def is_overdue(self, as_of: date,
                   chase_after_days: int = DEFAULT_CHASE_AFTER_DAYS) -> bool:
        return self.is_open and self.days_outstanding(as_of) >= chase_after_days

    def as_dict(self, as_of: date | None = None) -> dict:
        body = {
            "request_id": self.request_id,
            "case_id": self.case_id,
            "code": self.code.value,
            "label": CODE_LABELS[self.code],
            "sent_to": self.sent_to,
            "sent_at": self.sent_at,
            "status": self.status.value,
            "entry_id": self.entry_id,
            "channel": self.channel,
            "invite_id": self.invite_id,
            "replied_at": self.replied_at,
            "chases": self.chases,
            "note": self.note,
            "is_open": self.is_open,
        }
        if as_of is not None:
            # Derived on read — never stored. See the module docstring.
            body["days_outstanding"] = self.days_outstanding(as_of)
            body["overdue"] = self.is_overdue(as_of)
        return body


def new_request_id() -> str:
    return f"vreq_{uuid.uuid4().hex[:16]}"


def record(
    *,
    case_id: str,
    code: RequestCode,
    sent_to: str,
    sent_at: date,
    entry_id: str = "",
    channel: str = "",
    invite_id: str = "",
    chases: str = "",
    note: str = "",
) -> VerificationRequest:
    if not sent_to.strip():
        raise RequestError(
            "a request must record WHO it went to — an unaddressed request "
            "cannot be chased, and cannot be evidenced to an auditor")
    if code is RequestCode.CL and not chases:
        raise RequestError(
            "a chaser must name the request it follows up")
    return VerificationRequest(
        request_id=new_request_id(), case_id=case_id, code=code,
        sent_to=sent_to.strip(), sent_at=sent_at.isoformat(),
        entry_id=entry_id, channel=channel, invite_id=invite_id,
        chases=chases, note=note.strip(),
    )


def code_for_invite(kind: str, entry_type: str = "") -> RequestCode:
    """Which request an invite represents.

    An applicant link is a documentation request; a referee link's code depends
    on what is being confirmed, which is why the career entry type is consulted
    rather than assuming every referee is a work reference.
    """
    if (kind or "").upper() == "APPLICANT":
        return RequestCode.DR
    mapping = {
        "EDUCATION": RequestCode.ER,
        "SELF_EMPLOYMENT": RequestCode.AR,
        "UNEMPLOYMENT": RequestCode.GR,
        "CAREER_BREAK": RequestCode.GR,
        "RESIDENCE_ABROAD": RequestCode.GR,
        "TRAVEL_ABROAD": RequestCode.GR,
    }
    return mapping.get((entry_type or "").upper(), RequestCode.WR)


@dataclass(frozen=True)
class RequestSummary:
    open_count: int
    overdue_count: int
    closed_count: int
    overdue: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "open": self.open_count,
            "overdue": self.overdue_count,
            "closed": self.closed_count,
            "overdue_requests": list(self.overdue),
        }


def summarise(
    requests: list[VerificationRequest],
    as_of: date,
    chase_after_days: int = DEFAULT_CHASE_AFTER_DAYS,
) -> RequestSummary:
    open_reqs = [r for r in requests if r.is_open]
    overdue = [r for r in open_reqs if r.is_overdue(as_of, chase_after_days)]
    return RequestSummary(
        open_count=len(open_reqs),
        overdue_count=len(overdue),
        closed_count=len(requests) - len(open_reqs),
        overdue=tuple(r.request_id for r in overdue),
    )
