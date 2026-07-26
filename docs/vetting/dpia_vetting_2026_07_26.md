# Data Protection Impact Assessment — ARIA Vetting

**Status: DRAFT — pending review and sign-off by qualified counsel / the DPO.**
This document is not a legal opinion. It is an engineering-authored description
of what the system actually does, written so that a qualified reviewer can
assess it against fact rather than against a sales description. Every technical
claim below cites the file that implements it, so each can be verified.

- **Version:** 0.1 (draft)
- **Prepared:** 2026-07-26
- **Covers:** `aria_service/vetting/**`, `aria_service/routes/vetting.py`,
  `public/vetting.html`, `/api/v1/vetting/*`, MCP `vetting_*` tools
- **Reviewed by:** _(unassigned — required before processing begins)_
- **Next review:** _(set at sign-off; and on any change to the data flows in §2)_

---

## 1. Why a DPIA is required

Art. 35(3) UK GDPR and the ICO's list of mandatory-DPIA processing both bite:

- processing of **criminal-conviction and offence data** (Art. 10) about
  identifiable individuals, at scale;
- **evaluation or scoring** of individuals — the module produces a screening
  status and clause-referenced findings about a person;
- data concerning **vulnerable data subjects**: a job applicant is in an
  imbalanced relationship with the prospective employer and cannot freely
  refuse the processing;
- processing that may **deny someone employment**.

A DPIA is therefore mandatory, not discretionary.

---

## 2. Systematic description of the processing (Art. 35(7)(a))

### 2.1 Roles

| Party | Role | Basis |
|---|---|---|
| Employer / screening customer (the "tenant") | **Controller** | determines purpose and means of screening a named applicant |
| Arkmurus / ARIA | **Processor** | processes on documented instructions; see the Art. 28 schedule |
| Approved LLM provider | **Sub-processor** | document classification only (§2.4) |

This mirrors the existing position for counterparty data in
`public/about/privacy.html` §10.

### 2.2 Categories of data

| Category | Examples | Article |
|---|---|---|
| Identity | name, DOB, previous names, address history, NI number | Art. 6 |
| Career history | employers, dates, gaps, references | Art. 6 |
| Financial | CCJ/IVA totals, bankruptcy, directorships | Art. 6 |
| **Criminal offence** | convictions declaration, DBS/Disclosure certificate, NPCC police letter, SIA licence | **Art. 10 + DPA 2018 Sch. 1** |
| Documents | passports, payslips, P60/P45, bank statements, DWP letters | mixed |

No Art. 9 special-category data is sought. Health or similar data appearing
incidentally in an uploaded document is not extracted and is not indexed.

### 2.3 Data flow

1. The controller creates a case (`POST /api/aria/vetting/cases`). The case is
   pinned to an immutable rule pack (`CaseManifest`), so the rules applied are
   reproducible for the life of the file.
2. Documents are uploaded (`POST .../documents`), **encrypted with a per-case
   AES-256-GCM key before persistence** (`vetting/crypto.py`), and appended to
   the content-addressed evidence store (`intel/dd_evidence_store.py`).
3. A document is classified by an approved LLM sub-processor (§2.4). The model
   proposes a document type and covered dates; it **does not** assess the
   person (`vetting/documents.py`).
4. A deterministic rule engine computes gaps, clocks, thresholds and checklist
   state (`vetting/rules.py`). It reads no clock and performs no I/O, so any
   assessment can be replayed byte-identically.
5. A **named human** records the employment decision
   (`vetting/decisions.py`). The engine's status at that moment is stored
   alongside it.
6. The file is retained on a pack-declared schedule and then disposed of
   (`vetting/retention.py`).

### 2.4 Transfers and sub-processors

The platform's general LLM chain routes to DeepSeek (China — **no adequacy
decision**), disclosed in `public/about/privacy.html` §8.

**Vetting data is carved out of that chain entirely.** `vetting/processors.py`
builds a single approved processor and **fails closed** if none is available:
if no approved processor can be constructed, no extraction occurs and the
document is routed to a human instead. The allowlist defaults to Anthropic and
is controlled by `ARIA_VETTING_LLM_PROVIDERS`. Adding a name to it is a
data-protection decision requiring a processor agreement and a transfer
mechanism — not a performance tuning choice.

*Reviewer action:* confirm the transfer mechanism (UK IDTA / Addendum to the
EU SCCs) for each allowlisted provider before go-live.

### 2.5 Retention

| File type | Period | Anchor |
|---|---|---|
| Unsuccessful / withdrawn | 12 months | outcome date |
| Post-employment | 7 years | **end of employment** |
| Screening in progress | none | no clock has started |

Periods come from the jurisdiction pack, not from hardcoded policy. The
post-employment clock is anchored to the end of employment, not its start — a
file for a current employee correctly has no disposal date.

---

## 3. Necessity and proportionality (Art. 35(7)(b))

- **Purpose:** to establish whether an applicant meets a screening standard
  (BS 7858:2019 in the UK pack) before engagement in a security-relevant role.
- **Necessity:** the standard requires a verified account of the screening
  period. The 5/10-year window and the 31-day gap rule come from the standard;
  the module does not invent a broader collection.
- **Data minimisation:** the module collects only what the pack's checklist
  requires; the checklist is data, inspectable at `GET /api/aria/vetting/packs`.
- **Proportionality of the Art. 10 element:** criminal-offence data is
  processed only where the tenant has recorded a Schedule 1 condition and its
  appropriate policy document. Cases that do not hold conviction data never
  engage Art. 10 and are not asked to.
- **Alternative considered and rejected:** a fully automated adjudication
  would be cheaper and is technically available. It is rejected — see §4.1.

---

## 4. Risks to rights and freedoms (Art. 35(7)(c)) and mitigations (Art. 35(7)(d))

### 4.1 A person is refused work by an automated decision
**Risk: high.** Art. 22 engaged; a hiring rejection is a decision with
significant effect.
**Mitigation, enforced in code:** `vetting/decisions.py` refuses to record a
decision attributed to the system; adverse decisions require a stated reason
and a second reviewer (four-eyes); approving over open blockers requires a
recorded override. The engine status at decision time is stored, so *whether
any human ever departs from the recommendation* is auditable — the question
that distinguishes meaningful human involvement from a rubber stamp.
**Residual risk: low-medium.** Code cannot compel a reviewer to think. The
"departed from engine" statistic is the monitoring control; the controller
should review it periodically.

### 4.2 A false adverse finding
**Risk: high.** A wrong gap or a misread document could cost someone a job.
**Mitigation:** all findings are deterministic and clause-referenced, so any
finding can be traced to a rule and a date. Model output cannot create a
finding — it only proposes what a document *is*. Low-confidence
classifications and any authenticity concern route to a human and are never
silently accepted. The applicant can contest via
`POST .../dispute`, and disputes are appended to the file, never applied over
the evidence.
**Residual risk: low.**

### 4.3 Model misreads a document and it is trusted
**Risk: medium.**
**Mitigation:** a confidence floor (0.70); any authenticity concern forces
human review even at high confidence; an incoherent coverage window is dropped
and flagged rather than normalised; **PDFs and images are not parsed at all** —
they yield no text and route to a human, rather than producing a
plausible-looking extraction from bytes never decoded. An LLM failure yields
*no* extraction and is flagged `extraction_unavailable`, so a failed read can
never resemble a read that found nothing wrong.
**Residual risk: low.**

### 4.4 Personal data sent to an unapproved jurisdiction
**Risk: was high — see §2.4.**
**Mitigation:** fail-closed approved-processor list; a detector additionally
records a `data_protection_violation` gap if any reply is ever served by an
unapproved provider.
**Residual risk: low**, contingent on the reviewer confirming transfer
mechanisms per provider.

### 4.5 Cross-tenant leakage
**Risk: high** — disclosing that a named person is under screening by a named
employer is harmful even without the file contents.
**Mitigation:** the tenant is part of the database primary key, so an
un-scoped read is inexpressible; the tenant is pinned from the JWT by
`server.mjs` and is never client-selectable; reads fail **closed to 404, never
403**, so existence is not confirmed. A blank tenant reads nothing.
**Residual risk: low.**

### 4.6 Data cannot be erased on request
**Risk: was high.** The evidence store is append-only and exposes no delete.
**Mitigation:** per-case crypto-shredding (§2.3 step 2). Disposal destroys the
key in the same transaction as the case record, making retained ciphertext
irrecoverable while the evidence spine keeps its tamper-evidence. Documents
stored before encryption was enabled are reported as residue rather than
absorbed into a clean result.
**Residual risk: low.**

### 4.7 Excessive retention
**Mitigation:** pack-declared periods, a retention endpoint listing overdue
cases, and a UI banner. Disposal is operator-initiated, not automatic — a
deletion that fires unattended on a live personnel file is a worse failure
than one that waits.
**Residual risk: medium** — depends on the controller actually acting on the
overdue list. *Reviewer action: decide whether automatic disposal should be
offered.*

### 4.8 Applicant is unaware of the processing
**Risk: medium.** Art. 13/14 transparency sits with the **controller**, who
has the relationship with the applicant.
**Mitigation:** `GET .../subject-access` produces an Art. 15 export including
lawful basis, retention, decisions and an explicit
`automated_decision_making: false`.
**Residual risk: medium** — the processor cannot discharge the controller's
notice duty. *Controller action: issue a privacy notice at the applicant-link
step.*

### 4.9 Over-broad access via API / MCP
**Risk: medium.** MCP clients are typically LLMs.
**Mitigation:** vetting requires an explicit `vetting` scope that is **not**
granted by default and is absent from every pre-existing key; unscoped tools
are invisible in `tools/list` and indistinguishable from nonexistent on call;
both surfaces consume the same rate limit and daily quota.
**Residual risk: medium.** *Reviewer action: consider whether vetting tools
should be exposed over MCP to third-party LLM clients at all, or restricted to
first-party use.*

---

## 5. Outstanding actions before go-live

| # | Action | Owner |
|---|---|---|
| 1 | Select the Schedule 1 condition per customer and issue the APD | DPO / counsel |
| 2 | Confirm the transfer mechanism for each allowlisted LLM provider | counsel |
| 3 | Execute Art. 28 terms with each controller (see the processor schedule) | commercial |
| 4 | Controller-side privacy notice at the applicant-link step | customer |
| 5 | Decide on MCP exposure of vetting tools (§4.9) | operator |
| 6 | Decide whether automatic disposal is offered (§4.7) | operator + counsel |
| 7 | Sign off this DPIA and set a review date | DPO |

**Consultation with the ICO (Art. 36)** is required only if a high residual
risk remains after mitigation. On the current assessment no residual risk is
rated high — but that conclusion is for the reviewer to confirm, not for this
draft to assert.
