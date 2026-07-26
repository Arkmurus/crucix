# Appropriate Policy Document — criminal-offence data (ARIA Vetting)

**Status: DRAFT TEMPLATE — pending review and adoption by qualified counsel.**

This is the document required by **DPA 2018 Schedule 1, Part 4, paragraph 5**
whenever a Schedule 1 Part 1 or Part 2 condition is relied on to process
criminal-conviction or offence data.

Three things about it are commonly got wrong, so they are stated up front:

1. It must be **in place at the time of the processing** — not written
   afterwards when a request or an audit arrives.
2. It must be **kept under review** (para 5(2)(b)). An APD written once and
   never revisited stops being an appropriate policy document.
3. It must be **retained until six months after the processing ends**, and
   made available to the Information Commissioner on request (para 5(3)-(4)).

The system enforces the first two structurally: `POST /api/aria/vetting/legal-basis`
refuses a position with no APD reference or no review date, and refuses one
whose review date has passed. It cannot assess whether the document itself is
adequate — only that someone was required to produce one and to keep it current.

**Each controller needs their own adopted version of this document.** Adopting
this template unchanged is not sufficient: §2 and §3 require entries specific to
the controller's purpose and sector.

---

## 1. Controller

- **Controller:** _(the employer / screening customer)_
- **Prepared by:** _(name, role)_
- **Adopted:** _(date)_
- **Review date:** _(date — must be in the future; the system rejects a lapsed one)_
- **Retention of this document:** until six months after the relevant
  processing ends.

## 2. Condition(s) relied on

State the Schedule 1 condition and why it applies to *this* controller's
processing. Select one:

| Code (as recorded in the system) | Condition | APD required |
|---|---|---|
| `SCH1_P1_1_EMPLOYMENT` | Pt 1 para 1 — employment, social security and social protection | Yes |
| `SCH1_P2_10_UNLAWFUL_ACTS` | Pt 2 para 10 — preventing or detecting unlawful acts | Yes |
| `SCH1_P2_12_REGULATORY` | Pt 2 para 12 — regulatory requirements re unlawful acts / dishonesty | Yes |
| `SCH1_P2_18_SAFEGUARDING` | Pt 2 para 18 — safeguarding of children and individuals at risk | Yes |

> `SCH1_P3_33_LEGAL_CLAIMS` (Pt 3 para 33) needs no APD and is therefore the
> tempting selection. It covers processing necessary for legal proceedings and
> **does not authorise a routine screening programme**. The system refuses it
> for this purpose.

**Art. 6 basis:** recorded per case. Note that **consent is not available** —
in an employment relationship it is not freely given (Art. 4(11), Art. 7(4);
EDPB Opinion 2/2017). A BS 7858 signed screening authorisation evidences that
the screening was *authorised*; it is not the Art. 6 basis, and the two must
not be conflated.

## 3. Procedures for compliance with the Art. 5 principles

| Principle | How it is met |
|---|---|
| **Lawfulness, fairness, transparency** (5(1)(a)) | Art. 6 basis recorded per case; Art. 10 condition recorded per controller and enforced before conviction data can be stored; applicant privacy notice issued by the controller at the applicant-link step; Art. 15 export available. |
| **Purpose limitation** (5(1)(b)) | Data is processed solely to establish whether the applicant meets the screening standard. It is not reused for marketing, profiling, or model training. |
| **Data minimisation** (5(1)(c)) | Collection is bounded by the rule pack's checklist and screening period. Conviction data is sought only via the pack's accepted criminality routes. |
| **Accuracy** (5(1)(d)) | Findings are deterministic and clause-referenced. Model output cannot create a finding. The applicant may contest any finding, and disputes are recorded on the file. |
| **Storage limitation** (5(1)(e)) | Pack-declared retention: 12 months (unsuccessful) / 7 years from end of employment. Overdue files are listed and surfaced in the UI. |
| **Integrity and confidentiality** (5(1)(f)) | Per-case AES-256-GCM encryption at rest; tenant isolation enforced in the database primary key; reads fail closed; access requires an authenticated tenant; API/MCP access requires an explicit `vetting` scope not granted by default. |
| **Accountability** (5(2)) | Immutable rule-pack pinning per case; decisions attributed to a named human with the engine's status recorded alongside; evidence store is append-only and tamper-evident. |

## 4. Retention and erasure policy

- **Unsuccessful or withdrawn applications:** 12 months from the outcome date.
- **Successful applicants:** 7 years from the **end of employment**. The clock
  does not run from the start of employment; a current employee's file
  correctly has no disposal date.
- **In-progress screening:** no retention clock has started.
- **Erasure method:** disposal destroys the per-case encryption key in the same
  transaction as the case record. Because document artifacts are stored only as
  ciphertext, destroying the key renders them irrecoverable. An audit stub
  (which case, when, which document type, what digest) survives so the
  controller can evidence that the file existed and was disposed of on
  schedule, with no personal content behind it.
- **Exception, reported not hidden:** documents stored before per-case
  encryption was enabled are plaintext in an append-only store; destroying the
  key does not erase them. The system reports these explicitly rather than
  claiming a clean erasure.

## 5. Review

This document must be reviewed at least annually, and on any change to:
the Schedule 1 condition relied on; the categories of data collected; the
sub-processors used; or the retention periods.

The system will refuse further criminal-offence processing once the recorded
review date has passed — that refusal is the intended behaviour, not a fault.
