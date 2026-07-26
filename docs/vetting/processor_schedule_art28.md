# Art. 28 Processing Schedule — ARIA Vetting

**Status: DRAFT — pending review and settlement by qualified counsel.**
This is the processing schedule that accompanies a Data Processing Agreement.
It is written from the implementation, so a controller's reviewer can check the
commitments against the system rather than against a brochure. It is **not** a
complete DPA: the indemnities, liability caps, governing law and termination
terms are commercial drafting and are deliberately absent.

Existing position: `public/about/privacy.html` §10 already states that
Arkmurus acts as processor for counterparty data and will execute a DPA on
request. This schedule extends that to vetting, where the sensitivity is
materially higher.

---

## 1. Roles

- **Controller:** the employer / screening customer.
- **Processor:** Arkmurus (ARIA).
- The controller determines *whom* to screen, *which standard* applies, and
  *what the outcome is*. The processor supplies the mechanism.

The processor **never** makes the employment decision. This is enforced in
code, not merely promised: a decision cannot be recorded without a named human,
and the module contains no function that derives a decision from an assessment.

## 2. Subject matter, duration, nature and purpose (Art. 28(3))

| Item | Detail |
|---|---|
| **Subject matter** | Pre-employment screening of individuals identified by the controller |
| **Duration** | For the term of the agreement, plus the retention period in §6 |
| **Nature** | Collection, structuring, storage, deterministic rule evaluation, document classification, retrieval, erasure |
| **Purpose** | Establishing whether an applicant meets the controller's screening standard |

## 3. Categories of data subject and personal data

- **Data subjects:** job applicants and engaged personnel of the controller;
  named referees.
- **Personal data:** identity, address history, career history, financial
  status (CCJ/IVA, bankruptcy, directorships), and **criminal-conviction and
  offence data** (Art. 10) where the controller has recorded a Schedule 1
  condition.
- **Not sought:** Art. 9 special-category data. Where such data appears
  incidentally in a document it is not extracted and is not indexed.

## 4. Processor obligations (Art. 28(3)(a)–(h))

| Obligation | How it is met |
|---|---|
| **(a) Documented instructions only** | The processor acts on the controller's case data. Vetting data is not used to train models and is not reused for any other purpose. |
| **(b) Confidentiality of personnel** | Personnel with access are bound by confidentiality; the standard additionally requires screening staff to be screened themselves and prohibits self-screening. |
| **(c) Art. 32 security** | Per-case AES-256-GCM encryption at rest; tenant isolation in the database primary key; fail-closed reads (404, never 403); authenticated access; explicit non-default `vetting` scope for API/MCP; append-only tamper-evident evidence store; signed audit logs. |
| **(d) Sub-processors** | Listed in §5. The controller is notified of changes and may object. |
| **(e) Assistance with data-subject rights** | Art. 15 export endpoint; Art. 16/22(3) dispute recording; Art. 17 erasure by crypto-shredding; Art. 18/21 supported by the controller's ability to halt and dispose. |
| **(f) Assistance with Arts. 32–36** | This schedule, the DPIA draft, and the APD template are provided to support the controller's own assessment. |
| **(g) Return or deletion at end of contract** | On termination, cases are disposed of by the mechanism in §6 (or exported first, at the controller's election). |
| **(h) Information and audit** | The controller may request the DPIA, the sub-processor list, and evidence of the security measures above. |

## 5. Sub-processors

| Sub-processor | Purpose | Location | Transfer mechanism |
|---|---|---|---|
| Fly.io | Application hosting | United Kingdom (LHR) | n/a (UK) |
| Anthropic | Document classification only | United States | _(to be confirmed: UK IDTA / EU SCCs + Addendum)_ |

**Explicitly excluded:** the platform's general-purpose LLM chain routes to
DeepSeek (China, no adequacy decision). **Vetting data is carved out of that
chain.** The vetting extraction path builds a single approved processor and
**fails closed** — if no approved processor is available, no extraction occurs
and the document is routed to a human instead. No vetting personal data is sent
to a provider outside the approved list, by construction rather than by policy.

*Counsel action:* settle the transfer mechanism for each non-UK sub-processor
before go-live.

## 6. Retention and deletion

- Unsuccessful / withdrawn: **12 months** from the outcome date.
- Successful: **7 years** from the **end of employment**.
- Deletion destroys the per-case encryption key together with the case record.
  Document artifacts are stored only as ciphertext, so key destruction renders
  them irrecoverable. An audit stub survives to evidence that the file existed
  and was disposed of on schedule.
- The controller sets the outcome that starts the clock; the processor surfaces
  overdue files but does not delete unattended, because an automatic deletion
  firing on a live personnel file is a worse failure than one that waits.

## 7. Automated decision-making (Art. 22)

The processor does not make, and cannot make, an employment decision.

- Screening findings are produced by a **deterministic rule engine**; the same
  case and date reproduce the same findings byte-identically.
- The language model reads documents only. It proposes what a document *is*.
  It does not assess the person, and its output cannot create a finding.
- Every decision is recorded against a **named human**, with the engine's
  status at that moment stored alongside — so the controller can evidence
  meaningful human involvement, including how often a reviewer departs from the
  recommendation.
- Adverse decisions require a stated reason and a second reviewer.

## 8. Breach notification

The processor notifies the controller **without undue delay** on becoming aware
of a personal-data breach affecting the controller's data, with the information
the controller needs for its own Art. 33 notification.

## 9. Controller obligations (stated so the split is unambiguous)

The following sit with the controller and cannot be discharged by the processor:

1. Issuing the applicant privacy notice (Arts. 13–14) at the applicant-link step.
2. Selecting the Schedule 1 condition and issuing the Appropriate Policy
   Document — the system refuses to hold criminal-offence data until both are
   recorded.
3. Determining the Art. 6 lawful basis. **Consent is not available** in an
   employment context and is not offered by the system.
4. Making the employment decision, and responding to any challenge to it.
5. Completing its own DPIA. The processor's draft is provided as input.
