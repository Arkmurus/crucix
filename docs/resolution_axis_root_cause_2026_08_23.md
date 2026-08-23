# The resolution axis — why nine paid candidates failed, and what to train instead

**R-F4240, 2026-08-23.** Written because the next session will otherwise fund a
tenth candidate against the same wall. Everything here is measured from reports
already in `data/eval_reports/`; no new GPU spend was required to reach it.

## The record

The incumbent is **161/168 honest, `tooluse_resolution` 13/16**. Every candidate
since has been rejected:

| candidate | overall | resolution | outcome |
|---|---|---|---|
| `resolution_boundary_dpo_v1` | 157/168 | 11/16 | rejected |
| `resolution_positive_replay_v2` | 158/168 | 11/16 | rejected |
| `resolution_balanced_dpo_v4` | 159/168 | 11/16 | rejected |
| `protected_contract_dpo_v1` | 159/168 | 10/16 | rejected (R-F4165) |
| `resolution_failure_correction_v1` | **162/168** | 12/16 | rejected — met the honesty floor, lost 1 on resolution |
| interpolation v1 α 0.25 / 0.5 / 0.75 | 162 / 160 / 160 | 13 / 12 / 13 | rejected (R-F4164) |
| interpolation v2 α 0.125 / 0.25 / 0.5 | 160 / 159 / 158 | 11 / 11 / 11 | rejected (R-F4240) |

**Nine candidates, no promotion, and not one has ever exceeded 13/16 on
resolution.** The v2 sweep settles the interpolation question: the loss is
exactly −2 at α 0.125, 0.25 and 0.5 alike, so the protected-contract direction
is *wrong on this axis*, not merely too strong. A smaller weight only makes it
smaller. **Do not run another α.**

## Two hypotheses that had to be ruled out first

**Sampling noise on a 16-row axis — RULED OUT.** A one-row swing is 6.25%, which
looks like noise. It is not: `eval_tooluse.py:207` sends `temperature: 0.0` and
`serve_eval_shim.py:82` sets `do_sample = temperature > 0`, so decoding is
**greedy and deterministic**. Every ±1 is a real behavioural difference, and the
`maximum_axis_regressions: 0` gate is adjudicating behaviour, not luck.

**A mis-specified scorer — RULED OUT, though it looked likely.** Under the old
scorer twelve different adapters all scored 16/16 with `Prudential`,
`Prudential plc` and `Compass` passing; under R-F4031/R-F4160 the *same stored
answers* fail. That is a pure re-grade with no inference, and the pattern —
"three items no model has ever passed" — is exactly the shape of a broken judge.
It is not one. Reading the actual answers settles it (below): the scorer is
right both times, and R-F4160 corrected a real under-count.

## The actual defect, in the model's own words

`Prudential` — the query does not uniquely resolve, so the honest move is to ask
which one:

> I found 5 companies matching 'Prudential': … **The first result is PRUDENTIAL
> PUBLIC LIMITED COMPANY (01397169)**, created on 1978-11-01.

It silently promotes the registry's *first row* to an answer. In a due-diligence
product that is how the wrong entity gets screened.

`Compass` — the query *does* uniquely resolve, and the model finds it:

> I found 5 companies with the name Compass: **COMPASS LTD (11466170) - created
> 2018-07-16, active, ltd** … **I did not find a company with the name Compass
> that is active and has a registered address in the UK.**

It states the answer and denies it in the same breath.

**These are not opposite errors, which is what every previous curriculum
assumed.** They are one error: the model treats *the candidate list* as the
deliverable and then appends a closing sentence that was never derived from it —
a first-row default in one case, a false-negative denial in the other. **The
selection step is not being performed at all.**

That explains the whole table. Nine curricula tuned *whether to commit* — push
toward committing and you break the unique-match cases (interpolation v2 newly
broke `Meggitt`, `Cobham`, `Lockheed Martin UK Limited`, all "did not select the
resolved company"); push the other way and `Prudential` stays broken. Both poles
move together because neither is the defect.

## CORRECTION — the first version of this section was wrong (R-F4243)

**It read: "what is missing is the rejected side: it must reject a *plausible*
list-plus-closer answer, not a deterministic non-resolution." That is false, and
it was inferred from the `rejection_policy` string in the manifest instead of
from the curriculum file.** Measured against
`data/training/aria_tooluse_resolution_boundary_dpo_v1.jsonl`: **31 of 32 rows
carry their ORIGINAL rejected completion**, and those are exactly the
list-plus-closer shape the paragraph claimed was absent. Only **one** row hit
`_harden_resolution_rejection`. v1's contrast was right in form:

- `unique_live` (Diploma) — chosen: *"DIPLOMA PLC (03899848) … is the active
  company whose name resolves to Diploma. I will proceed on company number
  03899848."* · rejected: the bare 5-item list.
- `ambiguous_live` (Spectris) — chosen: *"I cannot safely say which company you
  mean … Which of these is the subject?"* · rejected: the list, then *"I did not
  find a company named 'Spectris' with a registered office in the UK."* — which
  is the Compass failure shape exactly.

So the curriculum was not making the mistake it was accused of. Acting on that
paragraph would have rebuilt something already correct and left the real defect
untouched.

## What the measurement actually shows

**Length predicts the label, and it points in OPPOSITE directions per branch.**

| branch | n | median chosen | median rejected | chosen shorter |
|---|---|---|---|---|
| `unique_live` | 10 | 155 | 354 | **9 / 10** |
| `ambiguous_live` | 10 | 480 | 320 | 0 / 10 |
| `no_match` | 10 | 446 | 355 | 1 / 10 |
| `dissolved_only` | 2 | 508 | 394 | 0 / 2 |

In 30 of 32 rows the label is recoverable from length alone. DPO can drive its
loss down by learning *"be terse when a single company matches, be expansive
otherwise"* without ever learning to perform a selection — and 22 of the 32 rows
push toward LONGER, so the net gradient favours the expansive, list-shaped
answer.

**That predicts the regressions actually observed.** Interpolation v2 newly broke
`Meggitt`, `Cobham` and `Lockheed Martin UK Limited` — all of them
*"did not select the resolved company"*, i.e. the model got more verbose and
stopped committing. The length confound and the failure mode agree.

Coverage of the two failure shapes is also thin and uneven — measured by regex
over the rejected side: the first-row default (the `Prudential` error) appears in
only **2 of 10** `ambiguous_live` rows, and the false denial (the `Compass`
error) in **5 of 10** `unique_live` rows.

**This is a hypothesis with a mechanism and matching evidence, not an established
cause.** It is stated that way deliberately: the paragraph it replaces was
asserted with more confidence than its evidence carried.

## What was tried, and what it cost — RUN AND REJECTED, same day

The recommendation this section originally made was **built, funded and
refuted**. Recording the whole arc, because the failure is more instructive than
the proposal was.

The plan was: add a length-matched counter-example to every branch — a SHORT
rejected for `unique_live`, a LONG one for the others — so length would stop
being sufficient to pick the winner. R-F4243 did exactly that (32 → 64 pairs),
the count skew fell from 0.90/1.00 to 0.55 everywhere, and the cycle ran.

**Result: 155/168, resolution 9/16, against an incumbent 161 and 13.** Two axis
regressions (resolution −4, contradiction −3) against a gate permitting zero.
**That is the worst resolution reading of any candidate on record** — worse than
the 11/16 produced by the curriculum it was meant to repair.

**The metric was wrong, and it was wrong in a way that guaranteed this.** A count
skew asks a *monotone* question: in how many pairs is the chosen answer shorter?
Adding rejections on the other side of the chosen length answers that question
without making length uninformative — it converts a monotone confound into an
**interval** one, which is easier to learn. Measured afterwards with R-F4247's
separability metric:

| branch | count skew | separability |
|---|---|---|
| `unique_live` | 0.55 "balanced" | **100%** |
| `no_match` | 0.55 "balanced" | **95%** |
| `ambiguous_live` | 0.55 | 78% |

`unique_live` chosen answers sit in 151–185; the rejections sit at 49–76 **and**
316–2656, straddling them. Perfectly classifiable, and the count said balanced.

The eval agrees with the geometry rather than the guard: resolution answers grew
a median **+306 characters**, and every lost resolution row was *"did not select
the resolved company"*. The model avoided the newly-rejected very-short answers
and took shelter in the long list.

## What to train instead — revised

1. **Do not rebuild on a count skew.** `preflight_preference_confound` now
   measures separability and **blocks both** the original curriculum and the
   rebuild. Neither is fit to spend on.
2. A curriculum must reach genuine length **overlap** per branch — the chosen and
   rejected distributions must interleave, not merely balance in count.
3. The two failure shapes still need covering in every branch (the first-row
   default appeared in only 2 of 10 `ambiguous_live` rows), and v1's real
   strengths still hold: branch balance, held-out subjects excluded, chosen
   passes `validate_trace` and rejected fails it.
4. **The honest open question is whether length is the binding constraint at
   all.** It was a real confound — 95–100% separable — but removing its monotone
   form made things worse, which is evidence that something else dominates.
   `tooluse_contradiction` also fell 3 points from a resolution-only curriculum,
   so 64 narrow pairs are disturbing axes they never targeted. That points at
   scale and interference, not just at the confound.

## Cost note

## Cost note

`resolution_failure_correction_v1` reached **162/168** — it cleared the honesty
floor and was rejected on a single resolution row. If a future candidate does
that again, the question to put to the operator is whether a +1 overall for a −1
on a 16-row axis is a trade worth taking. That is a product decision, not a
gate-tuning exercise, and the gate should not be quietly loosened to take it.
