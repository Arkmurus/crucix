# 500-Q Evaluation Set v1 — Gap List

**Phase A gate #6.** Status as of seed module shipped: scaffold + 30 high-confidence questions live; 470 questions outstanding to reach the gate target.

## How this works

1. Seed module `aria_service/intel/eval_golden_seed.py` defines the taxonomy + the questions ARIA can author from code-grounded knowledge (constitution clauses + DD-layer behaviour + canonical refusals).
2. `POST /api/aria/eval/seed/load` ingests the seed into the existing `eval_runner` golden set. Idempotent — re-runs add only missing entries.
3. `GET /api/aria/eval/coverage` shows the per-category gap.
4. Operator extends by appending entries to `SEED_ENTRIES` and re-running the loader, OR by `POST /api/aria/eval/golden` for one-off additions.
5. `POST /api/aria/eval/run` executes the set against ARIA's chat path, producing pass/warn/fail per entry + regression delta.

## Taxonomy (500 target = sum of category targets)

| Category | Target | Seeded today | Operator owes | Authoring source |
|---|---|---|---|---|
| `clause_01` … `clause_23` (23 categories) | 5 each = 115 | ~20 across 14 clauses | ~95 | Code (constitution clause text + past incidents) — I can extend further; defence-domain test cases benefit from operator review |
| `dd_layer_1` … `dd_layer_10` (10 categories) | 10 each = 100 | 7 (1 per layer 1-7) | ~93 | Code (dd_orchestrator.py). Layers 8-10 need verification — I see 7 main + 5b/5c sub-layers in code; spec says 10 |
| `sanctions_divergence` | 50 | 2 | 48 | **Operator-domain.** Real-world divergence cases (US vs EU vs UK on specific entities) need defence-broker expertise and ideally legal review |
| `counter_intel` | 50 | 1 | 49 | **Operator-domain.** Real fraud / honey-trap / spear-phish patterns Arkmurus has actually seen; I have one canonical advance-fee example |
| `refusal_doc_no_text` | 15 | 2 | 13 | Code-grounded; I can extend |
| `refusal_authority_spoof` | 15 | 2 | 13 | Code-grounded; I can extend |
| `refusal_fake_tool_action` | 15 | 1 | 14 | Code-grounded; I can extend |
| `refusal_fabricated_id` | 10 | 1 | 9 | Code-grounded; I can extend |
| `refusal_outdated_officeholder` | 10 | 1 | 9 | Code-grounded; I can extend |
| `refusal_premise_injection` | 10 | 1 | 9 | Code-grounded; I can extend (compliance-premise variants are domain-rich) |
| `multi_lang_pt` | 10 | 1 | 9 | Mixed — Portuguese I can do natively; native review recommended |
| `multi_lang_fr` | 10 | 1 | 9 | Mixed |
| `multi_lang_ar` | 10 | 1 | 9 | **Operator should review.** Native Arabic phrasing on defence-export terminology benefits from a native check |
| `multi_lang_es` | 10 | 1 | 9 | Mixed |
| `multi_lang_ru` | 10 | 0 | 10 | **Operator should review.** Defence-Russian terminology |
| `multi_lang_zh` | 10 | 0 | 10 | **Operator should review.** Defence-Mandarin terminology |
| `multi_lang_ro` | 10 | 0 | 10 | I can draft basic; native review recommended |
| `multi_lang_tr` | 10 | 0 | 10 | I can draft basic; native review recommended |
| `multi_lang_sw` | 10 | 0 | 10 | **Operator should review.** Swahili defence terminology is sparse |
| `multi_lang_pl` | 10 | 0 | 10 | I can draft basic; native review recommended |
| `multi_lang_de` | 10 | 0 | 10 | I can draft basic; native review recommended |
| **TOTAL** | **500** | **~30** | **~470** | |

(Seeded counts are approximate — run `GET /api/aria/eval/coverage` for the live tally after `POST /api/aria/eval/seed/load`.)

## What I can extend in the next 2-3 sessions (code-grounded)

If you want me to keep going on the categories I can author credibly without operator domain input:

- **All 23 clauses → 5 each = +90 questions.** Each clause has past-incident anchors I can turn into test cases.
- **DD layers 1-7 → 10 each = +63 questions.** Variant scenarios per layer (sanctioned hit, ambiguous hit, no hit, network propagation, contradicting sources).
- **Refusal variants → fill remaining = +67 questions.** Each refusal scenario has ~15 distinct phrasings worth testing.
- **Multi-lang basics (PT, FR, ES, RO, TR, PL, DE) → +50 questions** I can draft; operator approves wording.

That's another ~270 questions purely from code + clause text. Combined with today's 30 = ~300. Remaining ~200 require operator domain input.

## What needs operator authorship (cannot credibly fake)

These categories *will* be wrong if I author them alone:

- **`sanctions_divergence`** (50): every case needs to be a real entity with real divergent listings. Inventing one ("Acme Trading Co is on OFAC but not EU") gives a worthless test. Source from Arkmurus's actual screening hits or from public OFAC/OFSI/EU update notes.
- **`counter_intel`** (50): real fraud emails Arkmurus has received, real honey-trap LinkedIn approaches, real spec-sheet diversions. Anonymise but use the real text.
- **`multi_lang_ar` / `_ru` / `_zh` / `_sw`** (40): native-speaker review at minimum; defence terminology in these languages is non-trivial.
- **DD layer-specific edge cases** (~30): cases where layer 5c commercial-coherence flagged something layer 4 compliance didn't, etc. Domain-specific.

## Recommended sequence

1. **This session (now):** ship seed module + endpoints + this doc. Operator runs `POST /api/aria/eval/seed/load` to verify pipeline.
2. **Next 1-2 sessions (code-grounded):** I extend clauses + DD layers + refusal categories to ~300 total entries. ~3-4 hours operator time (review only).
3. **Subsequent sessions (operator-led):** operator drafts sanctions_divergence + counter_intel batches; sends through me for formatting and ingestion. ~6-10 hours operator time over 2-3 weeks.
4. **Native-language review:** find 4 native speakers (PT, AR, RU, ZH at minimum) for ~30 min each. Optional but raises eval credibility.
5. **Freeze:** when coverage report shows ≥ 500 total and no category < 80% of target, mark v1 frozen (commit + tag). Phase A gate #6 → ✅.

## Verification

After each batch:

```
POST /api/aria/eval/seed/load
GET  /api/aria/eval/coverage
POST /api/aria/eval/run            # actually execute against current chat path
GET  /api/aria/eval/runs           # see pass-rate trend
```

Pass-rate target for Phase B exit gate: ≥ 80% on the frozen v1 set against ARIA's main chat path AND against ARIA-LLM v0.0 (the sovereign baseline).
