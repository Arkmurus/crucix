# Training-cycle runway — start here 2026-08-04

Everything that could be proven without a GPU is proven. What remains needs
RunPod credit, which is the operator's action. This is the whole runway in order.

---

## 1. State at close of 2026-08-03

| Thing | State | Evidence |
|---|---|---|
| Dataset pre-flight | ✅ **clear to train** | all 7 checks, `--strict`, exit 0 (below) |
| Corpus manifest | ✅ recorded | 45 files / 18,149 rows, `CONTAMINATION=NO` |
| Frozen 500-Q eval | ✅ intact | gate #6 `pass=True`, `pinned_hash == live_hash = a07b6af760ad7f44` |
| RunPod credit | ❌ **operator** | top-up pending |
| `ARIA_RUNPOD_POD_ID` | unset (correct) | §24 — scheduler stays a no-op; the cycle scripts start the pod |
| `RUNPOD_API_KEY` | ✅ set | verified in-process |
| `ARIA_RUNPOD_AUTOSTART` | `0` (stop-only) | §24 pre-shadow mode — will not auto-start a pod nobody asked for |
| aria-intel / web / wa | ✅ all live + verified | build_rev matches on all three |

## 2. The pre-flight — re-run it before spending anything

It is free, takes seconds, and is the §24 dataset condition. **Run it with
`--strict`.** Without `--strict` it prints "clear to train" while SKIPPING
checks — that is how the first run on 2026-08-03 looked green with 4 of 7
unproven.

```bash
python -m scripts.train.preflight_cycle \
  --train-file data/training/split_v1/train.jsonl \
  --eval-file  data/training/split_v1/eval.jsonl \
  --golden-set data/eval_frozen/aria_eval_500q.jsonl \
  --base-model mistralai/Mistral-7B-Instruct-v0.3 \
  --strict
```

Last result (2026-08-03, exit 0):

```
schema         656 rows valid
subjects       656 rows carry a subject
split          148 train / 50 eval entities, disjoint
contamination  0 of 488 train rows overlap 480 golden entities
render         656 rows render
length         longest 2711 tok <= 4096
base-model     mistralai/Mistral-7B-Instruct-v0.3 — mistral, vocab 32768, 32L, 4096d
-> clear to train.
```

Needs `transformers` + `jinja2` from `requirements-dev.txt` (R-F3670). Both are
pure-Python; the pre-flight loads only the **tokenizer**, never a model, so the
"PyTorch was not found" warning is expected and harmless. Without `jinja2` all
656 rows report *unrenderable*, which looks like a corrupt dataset rather than a
missing dev dep.

**If the corpus changed since 2026-08-03**, re-record the manifest first —
otherwise a checkpoint cannot be attributed to its exact inputs:

```bash
python scripts/admin/training_corpus_manifest.py --record   # 0 = clear, 1 = contamination, 2 = refused
```

## 3. Order of operations tomorrow

1. **Top up RunPod.**
2. Re-run the strict pre-flight (§2). If it exits non-zero, **stop** — do not
   start a paid cycle on unproven input. That refusal is the feature.
3. Re-record the manifest if the corpus moved.
4. Start the cycle. The cycle scripts start AND stop the pod (`serve_and_eval`
   pattern: resume → work → stop). Do **not** set `ARIA_RUNPOD_POD_ID` to make
   the scheduler start one — §24 keeps it unset deliberately so window-mode
   cannot auto-start a pod nobody needs.
5. Confirm the pod is **stopped** at the end. The scheduler runs stop-only and
   force-stops a pod found RUNNING outside 09:00–18:00 UK or without an active
   work-claim, so a forgotten pod survives at most one reconcile (~2 min) — but
   check anyway.

**Spend:** the weekly cycle (~$8–18/wk) is pre-approved under §24. An explicit
ask is still required for any single run projected **>$20**, or once
month-to-date GPU spend reaches **$80**.

## 4. What is NOT mechanised — read before signing off

§24's condition has two halves. The **dataset** half is now mechanical and
green. The **"pre-flight review of the training pipeline"** half is **not
mechanised and has not been done**. `preflight_cycle.py` proves the data can
train; it says nothing about whether the training *recipe* (hyperparameters,
LoRA config, judge, reward) is sound. Treat that as an open review item, not as
covered by the green above.

## 5. The autonomous-coding angle

`ARIA_CODER_ENABLED=1` went live 2026-08-03 in **stage-only** mode, rate-capped
to 6 fixes/hour (was 2000). Her output is the raw material for the
autonomous-coding side of the cycle (`scripts/train/prepare_code_sft.py`,
`autonomous_code_prep.py`, `run_code_sovereign_cycle.py`).

Two things to check before using her output as training data:

- **She has no track record yet.** Scoreboard was empty at close
  (`fixed 0 / gold 0 / blocked_ratio 1.0`). Anything she staged overnight is
  unreviewed. Do not mine it into a corpus until a human has judged quality —
  §24's condition is that training must be REAL.
- ⚠️ **`ARIA_SELF_IMPROVE_AUTO_DEPLOY=1` is already set.** The only thing holding
  auto-deploy is the gold-lane evidence gate: **20 fixed + 10 gold at ≤25%
  blocked**. The moment she earns that, auto-deploy starts **on its own, with no
  further prompt**. Review her staged output before she reaches the threshold.

Check both in one shot:

```
GET /api/aria/coder/scoreboard      # counts + gold-lane decision
GET /api/aria/self/staged           # what is waiting for review
```

## 6. Still open, unrelated to the cycle

- **No LLM vendor redundancy** — chain is `deepseek` + `deepseek_backup`,
  `general_vendor_depth: 1`, and **both timed out** during the 2026-08-03 sweep.
  Needs a funded second vendor. This is the one item nobody but the operator can
  close.
- **Sensor coverage: 25 of 623 nodes (4%)** have a live sensor, and every
  aria-web organ maps to 0 modules. The ecosystem rollup is now honest
  (R-F3667), but it is judging the estate on 4% of it.
- **`docs/suite_baseline.json`** — owned by the peer agent; needs a quiet tree.
