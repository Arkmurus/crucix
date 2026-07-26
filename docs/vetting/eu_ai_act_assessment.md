# EU AI Act — classification assessment for ARIA Vetting

**Status: DRAFT — pending confirmation by qualified counsel.**
Written from the implementation so a reviewer can assess scope against what the
system does. It reaches a *provisional* classification and states plainly which
questions it cannot settle.

- **Version:** 0.1 (draft)
- **Prepared:** 2026-07-26
- **Covers:** the same scope as `dpia_vetting_2026_07_26.md`

> **Why this document exists.** The GDPR work (Arts. 6, 10, 15, 17, 22, 28, 32,
> 35, 44) addressed data protection. It does **not** address the AI Act, which
> is a separate product-safety regime with its own obligations, and which bites
> hardest on exactly this use case. Treating GDPR compliance as covering the AI
> Act is the most likely way to be caught out on an EU launch.

---

## 1. Provisional classification: HIGH-RISK (Annex III)

Annex III point 4 designates as high-risk AI systems intended to be used for
**employment, workers management and access to self-employment**, including
systems intended to be used *for the recruitment or selection of natural
persons*, in particular to filter applications and evaluate candidates.

Pre-employment screening that contributes to whether a person is engaged falls
squarely in that description. The provisional classification is therefore
**high-risk**, and the burden is on us to show otherwise rather than to assume
it.

### 1.1 What is and is not "AI" here — this distinction matters

The module has two very different components, and conflating them would
misstate the scope in either direction:

| Component | Nature | In scope? |
|---|---|---|
| `vetting/rules.py` — gaps, clocks, thresholds, checklist | **Deterministic.** A pure function of (case, pack, as_of); no inference, no learned parameters, no adaptiveness | Arguably outside the AI-system definition (it does not infer; it computes a documented rule) |
| `vetting/documents.py` — document classification | **An LLM.** Infers a document type and covered dates from content | An AI system |

So the AI component's actual job is narrow: *deciding what a document is*. It
does not score the applicant, and its output cannot create a screening finding.
That materially reduces risk, but it does **not** by itself take the product out
of Annex III: the system as placed on the market is intended for use in
recruitment, and a component-level argument is not a scope exemption.

**Do not rely on the deterministic-engine argument to avoid classification.**
It is a strong mitigation and a weak exemption.

### 1.2 The Art. 6(3) filter

Art. 6(3) allows an Annex III system to escape high-risk status where it does
not pose a significant risk — for example where it performs a *narrow
procedural task* or is *preparatory* to a human assessment. There is a credible
argument here: the LLM performs document classification (a narrow procedural
task) preparatory to a human decision.

Two cautions:
1. The derogation **does not apply** where the system performs profiling of
   natural persons. Confirm that document classification is not profiling on
   these facts.
2. Relying on Art. 6(3) requires **documenting the assessment before placing on
   the market** and registering in the EU database. It is a documented
   derogation, not a silent one.

*Counsel action: decide whether to claim Art. 6(3) or to accept high-risk
classification. Claiming it is not obviously cheaper — the documentation duty
is substantial either way.*

---

## 2. Obligations if high-risk, and where we stand

Provider obligations (Arts. 9-15 and following). Assessed honestly, including
where we do not yet comply.

| Obligation | Status | Evidence / gap |
|---|---|---|
| **Art. 9** risk management system | **Partial** | The DPIA covers data-protection risk with per-risk residual ratings. An AI-Act risk management system is broader and must be continuous and iterative across the lifecycle. **Gap.** |
| **Art. 10** data and data governance | **Partial** | We do not train models on vetting data; we use a third-party model for classification only. Training-data governance duties fall largely on the model provider, but the provider/deployer split needs settling (§3). |
| **Art. 11** technical documentation | **Gap** | Annex IV documentation not yet produced. This assessment and the DPIA are inputs, not a substitute. |
| **Art. 12** record-keeping / logging | **Strong** | Automatic logging over the system's lifetime: every route is §21a-wired on success and failure; assessments are byte-identically replayable (no clock reads, immutable pinned rule packs); the evidence store is append-only and content-addressed; decisions are recorded with the engine state at decision time. |
| **Art. 13** transparency to deployers | **Partial** | Pack status, decision-eligibility and confidence are surfaced; instructions-for-use in the Annex IV sense are not yet written. |
| **Art. 14** human oversight | **Strong** | Enforced, not documented-only: a decision cannot be attributed to the system; adverse decisions need a reason and four-eyes; approving over blockers needs a recorded override; departures from the recommendation are measurable. Low-confidence or authenticity-flagged documents route to a human. |
| **Art. 15** accuracy, robustness, cybersecurity | **Partial** | Deterministic engine is exactly reproducible; extraction fails closed and never fabricates on failure; encryption at rest; tenant isolation in the primary key. Declared accuracy metrics for the classification component are **not** yet published. **Gap.** |
| **Art. 17** quality management system | **Gap** | Not formalised. |
| **Art. 43** conformity assessment + CE marking | **Gap** | Not performed. Operator/counsel action. |
| **Art. 49** registration in the EU database | **Gap** | Not performed. Operator action. |
| **Art. 50** transparency obligations | **Met in substance** | The subject-access export states `automated_decision_making: false` and explains that findings come from a deterministic engine. |

### 2.1 Prohibited practices — checked, and clear

Art. 5 prohibitions were checked against the implementation:

- **Emotion inference in the workplace** — *not present.* The extraction prompt
  instructs the model to classify the document only and explicitly forbids
  inferring suitability, character or risk.
- **Social scoring** — *not present.* No cross-context scoring of individuals.
- **Biometric categorisation** — *not present.* Passports are classified as
  documents; no facial or biometric processing occurs.

This should be re-checked on any change to the extraction prompt or to the
addition of any image-processing capability.

---

## 3. The provider / deployer question (unsettled)

The AI Act splits duties between the **provider** (places the system on the
market) and the **deployer** (uses it under its own authority). This has not
been settled and it changes who owes what:

- If Arkmurus provides the module to employers, Arkmurus is likely the
  **provider** and carries Arts. 9-15, 17, 43, 49.
- The employer is likely the **deployer** and carries Art. 26 duties —
  including **informing workers' representatives and affected workers** before
  putting a high-risk system into use, and human oversight in operation.
- Under Art. 25, a deployer who puts its name on the system, or substantially
  modifies it, becomes a provider. Relevant if we white-label.

*Counsel action: settle this before any EU customer, and reflect it in the
Art. 28 schedule alongside the data-protection roles (which are already
settled: controller = employer, processor = Arkmurus). The two splits are
independent and need not align.*

---

## 4. Interaction with the GDPR work already done

The AI Act and the GDPR overlap but do not substitute for one another:

- Art. 22 GDPR (no solely-automated decision) is addressed by the decision
  record. This supports the AI Act Art. 14 human-oversight case but is not the
  same obligation.
- The DPIA is not a Fundamental Rights Impact Assessment. Art. 27 requires a
  **FRIA** from certain deployers of Annex III systems; where required, the
  deployer performs it and we should supply the inputs.

---

## 5. Current recommendation

1. **Do not market the vetting module into the EU until §2's gaps are closed.**
   The UK path is materially further along: no AI Act equivalent is in force,
   and the Art. 10 regime for GB is reviewed and enforced in code.
2. The module already **refuses** to hold criminal-offence data for any
   jurisdiction without a reviewed Art. 10 regime — currently GB only — so an
   EU case cannot silently proceed on UK statute (`vetting/legal_basis.py`,
   R-F3162). That is a floor, not compliance.
3. Sequence: **UK first** → settle provider/deployer → Annex IV technical
   documentation + declared accuracy metrics → conformity assessment →
   registration → EU launch.

*Nothing in this document is legal advice. The classification in §1 is
provisional and the Art. 6(3) question in §1.2 is expressly left open for
counsel.*
