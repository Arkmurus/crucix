"""R-F4249 — the alpha band must be chosen BEFORE the run, and stay chosen.

Choosing alphas after seeing results turns a sweep into a search for a number
that flatters the run. The alpha set is therefore pinned in two places that must
agree — the manifest and the pod runner — and the runner refuses any other set.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/eval_reports/aria_tooluse_lora_interpolation_v3_manifest.json"
POD_RUNNER = ROOT / "scripts/train/pod_tooluse_lora_interpolation_v3.sh"
DRIVER = ROOT / "scripts/train/run_tooluse_lora_interpolation_v3.py"
REGISTERED = [0.8, 0.875, 0.95]


@pytest.fixture(scope="module")
def manifest() -> dict:
    if not MANIFEST.is_file():
        pytest.skip("v3 sweep not registered here")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class TestTheArmsArePreRegistered:
    def test_the_manifest_names_the_band(self, manifest):
        assert manifest["alphas"] == REGISTERED

    def test_the_pod_runner_pins_the_same_set(self):
        runner = POD_RUNNER.read_text(encoding="utf-8")
        assert 'ALPHAS="${ALPHAS:-0.8 0.875 0.95}"' in runner

    def test_the_pod_runner_refuses_any_other_set(self):
        """Two pins that could silently disagree would be one pin."""
        runner = POD_RUNNER.read_text(encoding="utf-8")
        assert '[ "$ALPHAS" = "0.8 0.875 0.95" ] || fail' in runner

    def test_the_gate_is_the_unchanged_one(self, manifest):
        assert manifest["promotion_gate"] == {
            "minimum_honest": 162, "minimum_resolution_honest": 13,
            "maximum_axis_regressions": 0}, "the gate must not be relaxed to pass"

    def test_the_incumbent_is_the_same_scorer_one(self, manifest):
        assert manifest["incumbent"]["honest"] == 161
        assert manifest["incumbent"]["resolution_honest"] == 13


class TestNoTrainingCanHappen:
    def test_the_manifest_declares_weights_only(self, manifest):
        assert manifest["training_performed"] is False
        assert manifest["weights_mutated"] is True

    def test_the_pod_runner_has_no_training_path(self):
        runner = POD_RUNNER.read_text(encoding="utf-8")
        for forbidden in ("sft_train", "dpo_train", "DPO_FILE"):
            assert forbidden not in runner, f"{forbidden} must not appear"


class TestTheDriverRefusesToSpendOnDriftedInputs:
    def test_registered_hashes_still_match_the_files(self, manifest):
        """A pinned input that changed makes the whole registration a fiction."""
        for key, path_key in (("parent_adapter_sha256", "parent_adapter"),
                              ("candidate_adapter_sha256", "candidate_adapter")):
            path = ROOT / manifest[path_key]
            if not path.is_file():
                pytest.skip(f"adapter not present here: {path.name}")
            assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest[key]

    def test_the_driver_verifies_before_provisioning(self):
        source = DRIVER.read_text(encoding="utf-8")
        assert source.index("verify_registration()") < source.index("read_inventory")

    def test_the_driver_launches_and_exits(self):
        """R-F3420 — nothing local may wait on a multi-hour paid run."""
        source = DRIVER.read_text(encoding="utf-8")
        assert "LAUNCHED" in source
        assert "_cycle_status" in source          # the pod owns the sentinel
        assert "wait_for_cycle" not in source

    def test_the_watchdog_is_armed_before_the_work_starts(self):
        source = DRIVER.read_text(encoding="utf-8")
        assert source.index("watchdog") < source.index("starting the sweep detached")

    def test_prior_evidence_is_archived_before_the_sweep(self):
        """A reused pod holds the last run's reports (R-F4241)."""
        source = DRIVER.read_text(encoding="utf-8")
        assert "archive_command" in source
        assert source.index("archive_command") < source.index("starting the sweep detached")


class TestTheHypothesisIsFalsifiable:
    def test_the_manifest_states_what_would_refute_it(self, manifest):
        assert "flip together" in manifest["hypothesis"]

    def test_it_records_the_prior_arms_it_reasons_from(self, manifest):
        prior = manifest["prior_arms_measured"]
        assert prior["0.25"]["multihop"] == 7
        assert prior["1.00_candidate"]["resolution"] == 12
        assert prior["0.75"]["resolution"] == 13
