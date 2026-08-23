"""R-F4245 — a killed local driver must never strand a paid run.

The incident: `run_tooluse_dpo.sh` was killed ~40 minutes into a paid cycle on
2026-08-23. The pod side was fine — work detached, watchdog armed, POD_ID/HOST/
PORT already written to a durable state file — so nothing was lost. Only the
local harvest step died. R-F4197 had already solved this once for ONE pod with
everything hardcoded, which is the shape that guarantees the next incident
writes another script.
"""
from __future__ import annotations

import json
import pathlib
import tarfile

import pytest

from scripts.train import harvest_cycle as hc

CURRENT = "R-F4160-evidence-aligned-clean-v4"


def _write_state(tmp_path: pathlib.Path, **overrides) -> pathlib.Path:
    fields = {"POD_ID": "l6o0j96gfqwqui", "HOST": "195.26.232.147", "PORT": "53673"}
    fields.update(overrides)
    path = tmp_path / "state"
    path.write_text("".join(f"{k}={v}\n" for k, v in fields.items() if v is not None),
                    encoding="utf-8")
    return path


def _report(path: pathlib.Path, *, rows: int, total: int | None = None,
            complete: bool = True, scorer: str | None = CURRENT) -> pathlib.Path:
    payload = {
        "complete": complete,
        "total": total if total is not None else rows,
        "honest": rows,
        "rows": [{"label": "a", "subject": f"s{i}", "honest": True} for i in range(rows)],
    }
    if scorer is not None:
        payload["scorer_version"] = scorer
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestTheDurableHandoffIsEnoughToRecover:
    def test_the_state_file_the_driver_writes_is_readable(self, tmp_path):
        state = hc.read_state(_write_state(tmp_path))
        assert state["POD_ID"] == "l6o0j96gfqwqui"
        assert state["HOST"] and state["PORT"]

    def test_comments_and_blank_lines_are_tolerated(self, tmp_path):
        path = tmp_path / "state"
        path.write_text("# written by the driver\n\nPOD_ID=abc\nHOST=1.2.3.4\nPORT=22\n",
                        encoding="utf-8")
        assert hc.read_state(path)["POD_ID"] == "abc"

    @pytest.mark.parametrize("missing", ["POD_ID", "HOST", "PORT"])
    def test_an_incomplete_handoff_raises_rather_than_guessing(self, tmp_path, missing):
        path = _write_state(tmp_path, **{missing: None})
        with pytest.raises(RuntimeError, match=missing):
            hc.read_state(path)

    def test_an_absent_state_file_names_the_path(self, tmp_path):
        with pytest.raises(RuntimeError, match="no cycle state file"):
            hc.read_state(tmp_path / "nope")


class TestAPartialReportIsNeverPublished:
    """The dangerous artefact: it parses, it carries an honest count, and
    nothing about it says the run stopped early."""

    def test_a_complete_report_validates(self, tmp_path):
        path = _report(tmp_path / "r.json", rows=168)
        assert hc.validate_report(path, 168)["honest"] == 168

    def test_a_short_report_is_refused(self, tmp_path):
        path = _report(tmp_path / "r.json", rows=23)
        with pytest.raises(RuntimeError, match="expected 168 rows"):
            hc.validate_report(path, 168)

    def test_a_report_that_does_not_declare_completeness_is_refused(self, tmp_path):
        path = _report(tmp_path / "r.json", rows=168, complete=False)
        with pytest.raises(RuntimeError, match="completeness"):
            hc.validate_report(path, 168)

    def test_a_row_count_disagreeing_with_total_is_refused(self, tmp_path):
        path = _report(tmp_path / "r.json", rows=160, total=168)
        with pytest.raises(RuntimeError, match="expected 168 rows"):
            hc.validate_report(path, 168)

    def test_an_unversioned_report_is_refused(self, tmp_path):
        """R-F4244 — a report with no scorer_version cannot be compared to
        anything, so publishing it just moves the problem downstream."""
        path = _report(tmp_path / "r.json", rows=168, scorer=None)
        with pytest.raises(RuntimeError, match="scorer_version"):
            hc.validate_report(path, 168)


class TestAdapterValidation:
    def _tarball(self, tmp_path: pathlib.Path, member: str) -> pathlib.Path:
        inner = tmp_path / "adapter_config.json"
        inner.write_text("{}", encoding="utf-8")
        bundle = tmp_path / "adapter.tgz"
        with tarfile.open(bundle, "w:gz") as archive:
            archive.add(inner, arcname=member)
        return bundle

    def test_a_real_adapter_bundle_validates(self, tmp_path):
        hc.validate_adapter(self._tarball(tmp_path, "checkpoint/adapter_config.json"))

    def test_a_bundle_without_an_adapter_is_refused(self, tmp_path):
        with pytest.raises(RuntimeError, match="adapter_config.json"):
            hc.validate_adapter(self._tarball(tmp_path, "notes/readme.txt"))


class TestHarvestDoesNotRequireAGpu:
    """R-F4241 makes a PAID start demand a confirmed GPU. Collection moves
    files — the R-F4167 rescue succeeded on a pod that came back with
    `runtime.gpus: []`. Requiring one here would strand results behind a host
    capacity problem that has nothing to do with reading a file."""

    def test_resume_for_harvest_passes_require_gpu_false(self, tmp_path, monkeypatch):
        seen = {}

        def fake_start(pod_id, key, **kwargs):
            seen.update(kwargs)
            return {"pod_id": pod_id, "host": "1.2.3.4", "port": "22"}

        monkeypatch.setattr(hc.por, "_key", lambda: "k")
        monkeypatch.setattr(hc.por, "_req", lambda method, path, key, body=None: (
            (200, {"desiredStatus": "EXITED"})))
        monkeypatch.setattr(hc.por, "start_and_wait", fake_start)
        monkeypatch.setattr(hc.por, "stop", lambda pod_id, key: True)
        monkeypatch.setattr(hc, "read_sentinel", lambda h, p: "0")
        monkeypatch.setattr(hc, "_pull", lambda *a, **k: False)

        with pytest.raises(RuntimeError):      # no report pulled — that is fine
            hc.harvest(_write_state(tmp_path),
                       reports=[(hc.REMOTE_REPORT, tmp_path / "out.json")],
                       adapter_out=None, diagnostics_out=None, expected_rows=168,
                       stop_attempts=1, stop_delay=0)
        assert seen.get("require_gpu") is False


class TestThePodIsAlwaysStopped:
    def test_a_failed_harvest_still_stops_the_pod(self, tmp_path, monkeypatch):
        """A pod left running after a failed collection is the bill nobody
        notices."""
        stopped = []
        monkeypatch.setattr(hc.por, "_key", lambda: "k")
        monkeypatch.setattr(hc.por, "_req", lambda method, path, key, body=None: (
            (200, {"desiredStatus": "RUNNING"})))
        monkeypatch.setattr(hc.por, "stop",
                            lambda pod_id, key: stopped.append(pod_id) or True)
        monkeypatch.setattr(hc, "read_sentinel", lambda h, p: "0")
        monkeypatch.setattr(hc, "_pull", lambda *a, **k: False)
        with pytest.raises(RuntimeError, match="report could not be pulled"):
            hc.harvest(_write_state(tmp_path),
                       reports=[(hc.REMOTE_REPORT, tmp_path / "out.json")],
                       adapter_out=None, diagnostics_out=None, expected_rows=168,
                       stop_attempts=1, stop_delay=0)
        assert stopped == ["l6o0j96gfqwqui"]

    def test_nothing_in_this_module_deletes_a_pod(self):
        source = pathlib.Path(hc.__file__).read_text(encoding="utf-8")
        import ast
        deletes = [n.value for n in ast.walk(ast.parse(source))
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and n.value.strip().upper() == "DELETE"]
        assert deletes == []


class TestPartialArtefactsNeverOverwriteGoodOnes:
    def test_the_destination_is_untouched_when_validation_fails(self, tmp_path,
                                                                monkeypatch):
        report_out = tmp_path / "published.json"
        report_out.write_text('{"trusted": true}', encoding="utf-8")

        def fake_pull(host, port, remote, local, **kwargs):
            _report(local, rows=23)          # a truncated run
            return True

        monkeypatch.setattr(hc.por, "_key", lambda: "k")
        monkeypatch.setattr(hc.por, "_req", lambda method, path, key, body=None: (
            (200, {"desiredStatus": "RUNNING"})))
        monkeypatch.setattr(hc.por, "stop", lambda pod_id, key: True)
        monkeypatch.setattr(hc, "read_sentinel", lambda h, p: "0")
        monkeypatch.setattr(hc, "_pull", fake_pull)
        with pytest.raises(RuntimeError):
            hc.harvest(_write_state(tmp_path),
                       reports=[(hc.REMOTE_REPORT, report_out)],
                       adapter_out=None, diagnostics_out=None, expected_rows=168,
                       stop_attempts=1, stop_delay=0)
        assert json.loads(report_out.read_text(encoding="utf-8")) == {"trusted": True}


class TestItCollectsASweepNotJustOneReport:
    """R-F4250 — a single hardcoded remote name made the "general" harvest
    general over DPO runs only. An interpolation sweep writes one report per
    arm, so the tool could not collect the very next run."""

    def test_every_named_report_is_pulled_and_published(self, tmp_path, monkeypatch):
        pulled = []

        def fake_pull(host, port, remote, local, **kwargs):
            pulled.append(remote)
            _report(local, rows=168)
            return True

        monkeypatch.setattr(hc.por, "_key", lambda: "k")
        monkeypatch.setattr(hc.por, "_req", lambda method, path, key, body=None: (
            (200, {"desiredStatus": "RUNNING"})))
        monkeypatch.setattr(hc.por, "stop", lambda pod_id, key: True)
        monkeypatch.setattr(hc, "read_sentinel", lambda h, p: "0")
        monkeypatch.setattr(hc, "_pull", fake_pull)
        arms = [(f"/workspace/eval/arm_{t}.json", tmp_path / f"arm_{t}.json")
                for t in ("08", "0875", "095")]
        outcome = hc.harvest(_write_state(tmp_path), reports=arms, adapter_out=None,
                             diagnostics_out=None, expected_rows=168,
                             stop_attempts=1, stop_delay=0)
        assert pulled == [remote for remote, _ in arms]
        assert set(outcome["reports"]) == {p.name for _, p in arms}
        assert all(p.is_file() for _, p in arms)

    def test_one_bad_arm_publishes_none_of_them(self, tmp_path, monkeypatch):
        """A sweep is adjudicated as a set; half a set is a misleading set."""
        def fake_pull(host, port, remote, local, **kwargs):
            _report(local, rows=168 if "good" in remote else 23)
            return True

        monkeypatch.setattr(hc.por, "_key", lambda: "k")
        monkeypatch.setattr(hc.por, "_req", lambda method, path, key, body=None: (
            (200, {"desiredStatus": "RUNNING"})))
        monkeypatch.setattr(hc.por, "stop", lambda pod_id, key: True)
        monkeypatch.setattr(hc, "read_sentinel", lambda h, p: "0")
        monkeypatch.setattr(hc, "_pull", fake_pull)
        arms = [("/workspace/eval/good.json", tmp_path / "good.json"),
                ("/workspace/eval/short.json", tmp_path / "short.json")]
        with pytest.raises(RuntimeError):
            hc.harvest(_write_state(tmp_path), reports=arms, adapter_out=None,
                       diagnostics_out=None, expected_rows=168,
                       stop_attempts=1, stop_delay=0)
        assert not (tmp_path / "good.json").is_file()
