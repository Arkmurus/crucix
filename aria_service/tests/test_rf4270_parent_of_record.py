"""R-F4270 / C-231 — a promotion that nothing consumes did not happen.

R-F4259 promoted `failure_correction_v1` (162/168) and recorded, in its own
manifest, that "the candidate becomes the accepted PARENT/incumbent for future
training cycles". Measured 2026-08-23, nothing in the tree consumed that:

  * every launcher still pins `PARENT=.../aria_tooluse_curve_sft_v5.tgz`, the
    REJECTED 161/168 parent, by sha256;
  * `preflight_training_recipe` approves `parent_mode: "accepted_adapter"`
    without ever asking WHICH adapter is accepted — the label had no referent,
    so a cycle continuing from the wrong parent passes the paid-spend gate;
  * `adjudicate_sweep --incumbent` is a free-text path, so the next sweep would
    have been adjudicated against 161 and a null change would have read +1.

These tests fail against the pre-fix tree and are written first on purpose.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train import parent_of_record as por  # noqa: E402
from scripts.train import preflight_training_recipe as preflight  # noqa: E402

PROMOTED_REPORT = "aria_tooluse_resolution_failure_correction_v1_rf4163_rescored.json"
PROMOTED_ADAPTER_SHA = "be037261d14bb184f6613f61b384260c961c4f384969a6e4c82e6d570780a0c4"
REJECTED_PARENT_SHA = "99030c720f6db869f1fb4829d3389ee98f49cb67fea7b5169ca2f1b90417dac8"


@pytest.fixture(scope="module")
def record() -> dict:
    rec = por.read_record()
    assert rec is not None, "no parent of record — the R-F4259 promotion is inert"
    return rec


# -- the record says what R-F4259 decided -----------------------------------

def test_record_is_the_arm_rf4259_promoted(record: dict) -> None:
    assert record["report"].endswith(PROMOTED_REPORT)
    assert record["honest"] == 162
    assert record["total"] == 168
    assert record["scorer_version"] == "R-F4160-evidence-aligned-clean-v4"
    assert record["promoted_by"]["r_number"] == "R-F4259"


def test_recorded_adapter_is_the_promoted_one_not_the_rejected_parent(record: dict) -> None:
    """The whole point: the next cycle must train FROM the promotion."""
    assert record["adapter_sha256"] == PROMOTED_ADAPTER_SHA
    assert record["adapter_sha256"] != REJECTED_PARENT_SHA
    assert "curve_sft_v5" not in record["adapter"]


def test_record_matches_the_verdict_file_it_cites(record: dict) -> None:
    verdict_path = ROOT / "data/eval_reports" / record["promoted_by"]["verdict"]
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert verdict["decision"] == "promote:failure_correction_v1"
    assert verdict["promotion_authorized"] is True
    arm = verdict["arms"][0]
    assert record["report_sha256"] == arm["report_sha256"]
    assert record["honest"] == arm["honest"]
    assert por.sha256(verdict_path) == record["promoted_by"]["verdict_sha256"]


def test_record_carries_the_advisory_axis_so_it_is_never_silently_dropped(record: dict) -> None:
    """Advisory means measured and reported (R-F4259), including here."""
    assert record["advisory_axes"] == ["tooluse_resolution"]
    assert record["axis_honest"]["tooluse_resolution"] == 12
    assert record["axis_honest"]["tooluse_adverse"] == 28


# -- registering is derived from the verdict, and refuses ---------------------

def test_register_refuses_a_verdict_that_did_not_promote(tmp_path: pathlib.Path) -> None:
    verdict = tmp_path / "v.json"
    verdict.write_text(json.dumps({"decision": "reject_all_arms",
                                   "promotion_authorized": False, "arms": []}),
                       encoding="utf-8")
    with pytest.raises(RuntimeError, match="did not promote"):
        por.build_record(verdict, adapter=tmp_path / "a.tgz", root=tmp_path)


def test_register_refuses_when_the_named_report_is_not_on_disk(tmp_path: pathlib.Path) -> None:
    verdict = tmp_path / "v.json"
    verdict.write_text(json.dumps({
        "r_number": "R-TEST", "decision": "promote:x", "promotion_authorized": True,
        "scorer_version": "s", "advisory_axes": [],
        "arms": [{"arm": "x", "report": "absent.json", "report_sha256": "0" * 64,
                  "honest": 1, "total": 168}]}), encoding="utf-8")
    adapter = tmp_path / "a.tgz"
    adapter.write_bytes(b"weights")
    with pytest.raises(RuntimeError, match="report"):
        por.build_record(verdict, adapter=adapter, root=tmp_path)


def test_register_refuses_a_missing_adapter(tmp_path: pathlib.Path) -> None:
    """A parent with no weights cannot parent anything."""
    verdict = ROOT / "data/eval_reports/aria_tooluse_promotion_rf4259_verdict.json"
    with pytest.raises(RuntimeError, match="adapter"):
        por.build_record(verdict, adapter=tmp_path / "gone.tgz", root=ROOT)


# -- the paid-spend gate now has a referent ----------------------------------

def _recipe(**over) -> dict:
    base = {
        "kind": "tooluse_positive_sft_scaled_continuation",
        "runner": "scripts/train/pod_tooluse_sft_continue.sh",
        "base_model": "mistralai/Mistral-7B-Instruct-v0.3",
        "epochs": 1, "learning_rate": 1e-6, "batch_size": 2,
        "max_sequence_length": 4096, "lora_rank": 32, "lora_alpha": 64,
        "load_in_4bit": True, "completion_only_loss": True,
        "parent_mode": "accepted_adapter",
        "parent_adapter_sha256": PROMOTED_ADAPTER_SHA,
    }
    base.update(over)
    return base


def _spend_gate(recipe: dict) -> int:
    """Drive the REAL paid-spend entry point the launcher calls (§3c)."""
    return preflight.main(["--recipe-json", json.dumps(recipe)])


def test_preflight_refuses_the_live_defect_a_cycle_pinned_to_the_rejected_parent() -> None:
    """THE CAPABILITY TEST — this exact recipe passed the gate before R-F4270."""
    recipe = _recipe(parent_adapter_sha256=REJECTED_PARENT_SHA)
    assert _spend_gate(recipe) == 3, "a cycle from the rejected parent was approved"
    assert any("parent_adapter_sha256" in e for e in preflight.validate_parent(recipe))


def test_preflight_refuses_accepted_adapter_mode_that_declares_no_parent() -> None:
    """Omitting the field must not be the way through the gate."""
    recipe = _recipe()
    recipe.pop("parent_adapter_sha256")
    assert _spend_gate(recipe) == 3


def test_preflight_APPROVES_the_true_accepted_parent() -> None:
    """A gate that cannot open is not a gate (the R-F4263 lesson)."""
    assert _spend_gate(_recipe()) == 0


def test_preflight_blocks_when_the_record_cannot_be_read(monkeypatch) -> None:
    """Absence is 'I could not measure', never permission to spend."""
    monkeypatch.setattr(por, "read_record", lambda *a, **k: None)
    assert _spend_gate(_recipe()) == 3
    assert any("parent of record" in e for e in preflight.validate_parent(_recipe()))


def test_the_pure_recipe_checker_is_left_pure() -> None:
    """R-F4270 must not widen `validate_recipe` — eleven tests pin it as pure,
    and a historical fixture must never be made to name a parent it never used."""
    recipe = _recipe()
    recipe.pop("parent_adapter_sha256")
    assert preflight.validate_recipe(recipe) == []


def test_diagnostic_candidate_mode_is_untouched() -> None:
    """This fix must not reach recipes that deliberately fork off a candidate."""
    recipe = _recipe(kind="tooluse_positive_sft_scaled_diagnostic_continuation",
                     parent_mode="diagnostic_candidate")
    recipe.pop("parent_adapter_sha256")
    assert preflight.validate_parent(recipe) == []


# -- adjudication compares against the promotion, not against a stale path ----

def test_adjudicate_refuses_an_incumbent_that_is_not_the_parent_of_record(
        tmp_path: pathlib.Path) -> None:
    """Adjudicating against 161 would score a null change as +1."""
    out = subprocess.run(
        [sys.executable, "-m", "scripts.train.adjudicate_sweep",
         "--manifest", "data/eval_reports/aria_tooluse_promotion_rf4259_manifest.json",
         "--incumbent", "data/eval_reports/aria_tooluse_incumbent_rf4160_rescored.json",
         "--arm", f"failure_correction_v1=data/eval_reports/{PROMOTED_REPORT}"],
        cwd=ROOT, capture_output=True, text=True)
    assert out.returncode != 0
    assert "parent of record" in (out.stdout + out.stderr).lower()


def test_adjudicate_defaults_to_the_parent_of_record(record: dict) -> None:
    """With no --incumbent, the sweep uses what was actually promoted."""
    out = subprocess.run(
        [sys.executable, "-m", "scripts.train.adjudicate_sweep",
         "--manifest", "data/eval_reports/aria_tooluse_promotion_rf4259_manifest.json",
         "--arm", f"failure_correction_v1=data/eval_reports/{PROMOTED_REPORT}"],
        cwd=ROOT, capture_output=True, text=True)
    # The promoted arm re-adjudicated against ITSELF is a null change: gain 0.
    assert "incumbent 162/168" in out.stdout, out.stdout + out.stderr
    assert "+0" in out.stdout


def test_an_explicit_override_is_allowed_but_must_carry_a_reason() -> None:
    """Historical re-runs stay possible — they just have to say so."""
    out = subprocess.run(
        [sys.executable, "-m", "scripts.train.adjudicate_sweep",
         "--manifest", "data/eval_reports/aria_tooluse_promotion_rf4259_manifest.json",
         "--incumbent", "data/eval_reports/aria_tooluse_incumbent_rf4160_rescored.json",
         "--incumbent-override", "re-running the historical R-F4259 adjudication",
         "--arm", f"failure_correction_v1=data/eval_reports/{PROMOTED_REPORT}"],
        cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "incumbent 161/168" in out.stdout


def test_the_launcher_names_the_parent_it_continues_from() -> None:
    """The gate needs the field; this is the only thing that supplies it."""
    launcher = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    assert 'parent_adapter_sha256' in launcher
    assert '$ADAPTER_SHA256' in launcher
    # scoped: a diagnostic fork must not be made to claim the accepted parent
    injection = launcher.split('if [ "$PARENT_MODE" = accepted_adapter ]; then', 1)
    assert len(injection) == 2, "the injection is not scoped to accepted_adapter"
    assert "parent_adapter_sha256" in injection[1].split("fi", 1)[0]


def test_launcher_and_gate_actually_connect(record: dict) -> None:
    """END TO END — the launcher's own snippet, fed to the real gate.

    Two halves that are each correct and do not meet is how this defect existed
    in the first place: `parent_mode` was emitted and checked, and meant nothing.
    """
    import shutil
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash unavailable")
    template = ('{"kind":"tooluse_positive_sft_scaled_continuation",'
                '"runner":"scripts/train/pod_tooluse_sft_continue.sh",'
                '"base_model":"mistralai/Mistral-7B-Instruct-v0.3","epochs":1,'
                '"learning_rate":1e-6,"batch_size":2,"max_sequence_length":4096,'
                '"lora_rank":32,"lora_alpha":64,"load_in_4bit":true,'
                '"completion_only_loss":true,"parent_mode":"accepted_adapter"}')

    def emit(adapter_sha: str) -> str:
        script = (f'RECIPE_JSON={template!r}\n'
                  f'ADAPTER_SHA256={adapter_sha}\nPARENT_MODE=accepted_adapter\n'
                  'if [ "$PARENT_MODE" = accepted_adapter ]; then\n'
                  '  RECIPE_JSON="${RECIPE_JSON%\\}},\\"parent_adapter_sha256\\":'
                  '\\"$ADAPTER_SHA256\\"}"\nfi\nprintf %s "$RECIPE_JSON"\n')
        done = subprocess.run([bash, "-c", script], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        return done.stdout

    # the accepted parent -> the cycle is allowed to spend
    approved = emit(record["adapter_sha256"])
    assert json.loads(approved)["parent_adapter_sha256"] == record["adapter_sha256"]
    assert preflight.main(["--recipe-json", approved]) == 0

    # the parent the promotion replaced -> refused
    assert preflight.main(["--recipe-json", emit(REJECTED_PARENT_SHA)]) == 3


def test_the_module_never_deletes_a_checkpoint() -> None:
    """Same contract as pod_of_record: evidence is never destroyed here."""
    source = (ROOT / "scripts/train/parent_of_record.py").read_text(encoding="utf-8")
    for forbidden in ("unlink(", "rmtree", "os.remove"):
        assert forbidden not in source


# -- R-F4285 · evidence is identified by CONTENT, not by line terminators ----

def test_a_line_ending_change_is_not_a_change_of_evidence(tmp_path) -> None:
    """R-F4283 renormalised these artifacts to LF and every recorded sha broke.

    The R-F4259 verdict's hash was taken over CRLF bytes on Windows, so it would
    have refused on Linux and in CI all along. Both directions must verify.
    """
    import hashlib
    lf = tmp_path / "lf.json"
    lf.write_bytes(b'{\n  "a": 1\n}\n')
    crlf_digest = hashlib.sha256(b'{\r\n  "a": 1\r\n}\r\n').hexdigest()
    lf_digest = hashlib.sha256(lf.read_bytes()).hexdigest()

    assert por.matches_recorded(lf, lf_digest) == "exact"
    assert por.matches_recorded(lf, crlf_digest) == "crlf"

    crlf = tmp_path / "crlf.json"
    crlf.write_bytes(b'{\r\n  "a": 1\r\n}\r\n')
    assert por.matches_recorded(crlf, lf_digest) == "lf"


def test_a_real_content_change_is_still_refused(tmp_path) -> None:
    """The tolerance is line terminators ONLY. A guard that cannot bite is not one."""
    import hashlib
    f = tmp_path / "x.json"
    f.write_bytes(b'{\n  "a": 1\n}\n')
    other = hashlib.sha256(b'{\n  "a": 2\n}\n').hexdigest()
    assert por.matches_recorded(f, other) is None


def test_the_record_says_which_rendering_matched(record: dict) -> None:
    """Auditable: a reader can see the verdict's hash was a CRLF-era one."""
    assert record["report_sha256_match"] in ("exact", "lf", "crlf")
