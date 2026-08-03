# Design review — *ARIA: State of the Art, Approach & Design* v1.0 (02 Aug 2026)

Reviewed 2026-08-03 against **measured live state**, not against the repo's own
documentation. Evidence base for this review:

- a full Prospector run over `aria_service/` (32,313 messages, first time the
  repo's own profile has ever actually executed) + an ESLint pass over the Node
  tier (696 problems) — `docs/prospector_360_sweep_2026_08_03.md`
- a 15-cycle live log sweep across `aria-intel` / `aria-web` / `aria-wa`
- live probes of `/health`, `/phase/gates`, fly secrets, and the training manifest

## Verdict in one paragraph

**The doctrine is right and the sequencing is wrong.** Section 2 (design doctrine)
and §4.8–§4.9 (benchmarks, gated learning) are the strongest part of this
document and I would not change them. But the plan proposes building a cognitive
kernel, an evidence fabric, domain packs and sovereign inference on top of a
codebase where the measured failure mode is *silently broken plumbing* — and no
amount of architecture fixes an unauthenticated `fetch`. The doc's own doctrine
#2 ("Unknown is never success") is being violated **mechanically today**, and the
causes are ordinary software defects hidden by swallowed exceptions. That is
cheap to fix and it is the precondition for the document's central claim.

---

## 1. What the live system says about the doc's premises

### 1.1 The "Implemented" labels are mostly honest — with three corrections

| Doc claim | Measured |
|---|---|
| §3 "WA channel — Implemented" | **Half true.** `aria-wa` (standalone) is healthy: heartbeat every 3 min, `connected=true`, clean 428 reconnect. But the *embedded* listener in `aria-web` (`lib/whatsapp/waListener.mjs`, imported at `server.mjs:101`) threw `ReferenceError` on **every** @-mention — its whole LLM call had been deleted by a comment-merge in R-F1770. Fixed today (R-F3651). |
| §1.1 "KYC/AML/full-scope DD — Implemented" | Implemented, but with **silent** data gaps: the TBML screen produced nothing at all on every DD (keyword-only call swallowed by `except: continue`, fixed as R-F3647); OpenSanctions' monthly quota is exhausted; CCJ/Registry Trust unconfigured; Find Case Law unlicensed. The findings *are* gap-honest by design — but one of them was not a declared gap, it was an invisible one. |
| §1.6 "Autonomous SWE — Planned, governance-gated" | Accurate, but note the live state is worse than "planned": `gap_detector` **runs and queues gaps**, `ARIA_CODER_ENABLED=0` makes `self_coder` **refuse every one**. The loop burns cycles producing refusals. |

### 1.2 Two premises the doc understates

**(a) There is no vendor redundancy.** The live chain is
`["deepseek", "deepseek_backup"]` → `general_vendor_depth: 1`. During the sweep
**both entries timed out**. §4.5's model control plane is therefore not an
elegance play — it is addressing a live single point of failure. But the doc
reaches for *sovereign 2×H200* as the answer, when the cheap, immediate fix is a
**second vendor** in the fallback chain. Add that first; it is days of work, not
a GPU programme.

**(b) A frozen 500-question eval already exists and the doc does not mention it.**
Phase-A gate #6 is closed and *earned*: `pinned_hash == live_hash =
a07b6af760ad7f44`, count 500, frozen 2026-07-16 by the operator, and any edit
re-opens the gate. §4.8 proposes growing ARIA-DD / ARIA-TRUTH / etc. "seeded from
the gold set — the four adjudicated production reports are cases 1–4" with no
reference to the 500-Q set. Either they are complementary (say so, and say how
they compose) or the design was written without knowledge of it. **This matters
because §4.9's Gate G-T is defined in terms of "ARIA's suites"** — an ambiguous
term if there are two independent suites.

---

## 2. The systemic finding the doc should name

Prospector counts, across `aria_service/`:

- **1,117** × bandit `B110` — *try / except / pass*
- **4,822** × pylint `broad-exception-caught`

**Seven of the nine defects fixed today were invisible because of one of those.**
The pattern is always identical: a call that can *never* succeed (wrong arity,
missing import, missing auth header) sits inside `except Exception: pass` or
`except: continue`, so a permanent programming error is indistinguishable from
"no result found". Concretely, today:

- TBML screening returned nothing on every DD, for the life of the feature
- vault-source auto-suspension never reached the brain
- the entire curiosity-exploration loop has been dead behind a circuit breaker
  that reported the wrong cause

This is precisely doctrine #2 — *unknown is never success* — failing at the
plumbing layer. The evidence fabric in §4.3 is designed to enforce that contract
**for external sources**; it has nothing to say about ARIA's own internal calls
lying to her. The doc needs an eighth outcome class *for internal operations*, or
better: a mechanical gate (below).

**A recommendation the doc can adopt as-is:** the eight-way outcome distinction
in §4.3 should apply to **internal** calls too, and `except: pass` around any
call that can produce a finding should be a **lint error**, not a style
preference. A swallowed exception is an undeclared unknown.

---

## 3. Sequencing — the main change I would make

### 3.1 A naming collision that will cause real damage

The doc's **"Phase A (wks 0–4)"** is a read-only assessment. The repo's
`CLAUDE.md` §1 **"Phase A"** is the seven honesty gates, and it carries a binding
rule: *refuse Phase B+ work until ALL Phase A gates close*. These are different
things with the same name, and one of them is enforced by a rule that makes
agents refuse work. **Rename the doc's phases (P0/P1/P2/P3, or Programme 0–3)
before anyone executes against it.** This is the single highest-risk line in the
document and it is free to fix.

### 3.2 Where the gates actually stand (live, 2026-08-03)

**3 of 7 passing.**

| Gate | State |
|---|---|
| #1 composite ≥71% | `0.848` — **above target** but `pass=false`, `low_confidence` (confidence 0.30). Blocked on *confidence*, not score. |
| #2 heatmap floor ≥70% | **0.003.** Was 0.507. |
| #3 0 fly ERRORs/7d | fail — streak reset; 944,829 lifetime ledger entries |
| #4 quarantine closed | ✅ 4/4 |
| #5 env vars | ✅ — but *only* via `runtime_override=1`; the fly secret `ARIA_AUTONOMOUS_ENABLED` is still `"0"` |
| #6 500-Q frozen | ✅ earned, hash intact |
| #7 ≥4 design partners | fail — 1 engaged, 3 declined. **Operator-owned; no code closes this.** |

**Gate #2 at 0.003 is not a regression — it is the system becoming honest.**
R-F2660 replaced a reading-volume "trophy" (which credited mastery merely for
*finding* region text) with a real recall grade, and CLAUDE.md predicted the drop
in advance. The prior 0.507 was an artefact of `INITIAL_MASTERY = 0.5` plus one
touch. **The true competence baseline was always near zero and is only now
visible.**

This has a direct consequence for the design doc: **§4.9's "measured delta
justifies the cost" cannot currently be computed**, because the thing that would
measure it (gate #1) is `low_confidence`, and the breadth measure (gate #2) has
just reset to near-zero. Any training-spend decision taken this month would be
taken blind.

### 3.3 What I would insert before the doc's Phase A

A short **Programme −1: make failure visible** — cheap, mechanical, and the
precondition for everything the doc wants to claim:

1. **A semantic lint gate on the Node tier.** There is none today; `npm run lint`
   is `node --check` only. Four of today's nine defects — including a WhatsApp
   handler that could not reply *at all* — produce perfectly valid JavaScript and
   were invisible to it. Land ESLint as reporting-only first (696 existing
   problems), burn down, then make it blocking.
2. **A call-arity gate.** Prospector's `no-value-for-parameter` /
   `unexpected-keyword-arg` / `too-many-function-args` findings are *always*
   real: a keyword-only function called positionally can never succeed. Six such
   sites remain unfixed (listed in the sweep doc). This is a CI check, not a
   refactor.
3. **An auth-on-egress gate.** Every cross-service `fetch` must carry a token, or
   fail loudly. R-F3655/R-F3656 existed because nothing checked.
4. **An `except: pass` audit** over paths that can produce a finding.
5. **Wire `docs/suite_baseline.json`** — the §16 gate exists (`scripts/admin/suite_baseline.py`, R-F3373) but is stale (2026-07-28) and **not wired into `ci.yml`**.

None of this needs the kernel, the fabric, or a GPU. All of it is required before
"what the platform can prove" is a defensible claim.

---

## 4. Layer-by-layer notes

**§4.2 Cognitive kernel — agree, and the "one reasoning path" rule is the most
valuable sentence in the document.** The DD orchestrator is ~19k lines with 1,616
Prospector messages; wrapping it incrementally is right and a parallel cognition
system would be fatal. Add one constraint the doc omits: **the kernel must not be
built while the orchestrator still swallows its own failures**, or the typed
`Unknown` will faithfully record "no TBML anomalies" for a screen that never ran.

**§4.3 Evidence fabric — the strongest section.** The eight-way outcome
distinction is exactly right. Extend it to internal calls (§2 above).

**§4.5 Sovereign inference — right idea, wrong priority order.** Sequence:
(1) second vendor in the chain, (2) model control plane / routing / pinning,
(3) sovereign pilot. The doc's G3 gate (residency + provenance memos) is good and
should stay hard.

**§4.8 Benchmarks — reconcile with the existing frozen 500-Q set** before growing
new suites. Also: the doc says benchmarks are "grown from every reviewed report" —
with gate #7 at 1 design partner, the growth rate is currently ~zero. The
benchmark plan has a **customer-supply dependency** that the doc does not name.

**§4.9 Learning pipeline — keep exactly as written.** It matches live reality:
the training corpus manifest is clean and mechanical (57 files / 31,485 rows /
136 MB, `CONTAMINATION=NO` against the frozen pin), RunPod is in stop-only mode
with `ARIA_RUNPOD_POD_ID` unset, and `ARIA_LLM_URL` is not configured so the
trained adapter is not in the live chain. **Gate G-T is real and currently
holding.** My only amendment: state explicitly *which* suite G-T is measured on
(§1.2b).

**§4.10 Governance — add the authority level for the coder.** The live
`ARIA_CODER_ENABLED=0` question is exactly an L0–L6 question: the detector is
running at L0 (observe) while the fixer is barred from L2 (low-risk patches).
Framing it in the doc's own vocabulary makes it a decision rather than a stalled
flag.

---

## 5. What I would not change

The status vocabulary and the "never presents ambition as achievement" rule; the
non-goals list (§8); doctrine #2, #3 and #8; the two-memo bar on the word
"sovereign"; and G-T. These are the parts that make the document unusual, and
they are consistent with how the gates in this repo are already measured — the
gate code refuses to certify what it cannot prove, which is the same instinct.

## 6. Single most important recommendation

> **Insert Programme −1 (make failure visible), rename the phases so they do not
> collide with `CLAUDE.md` Phase A, and fix the LLM vendor monoculture — before
> any kernel, fabric, or GPU work.**

The document's thesis is that ARIA's moat is *what it can prove*. Today the
platform cannot yet prove that its own internal calls executed. That gap is
small, cheap and mechanical — and closing it is what makes the rest of this
design credible rather than aspirational.
