"""R-F4241 — the training fleet must stop growing, and nothing may be deleted.

The defect these pin: 70 pods on the live account, all EXITED, one per cycle,
billing $55.44/month of idle disk against a $3.92 balance. Every test below
fails on a tree where `decide` is allowed to create a pod it did not have to.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from scripts.train import pod_of_record as por


def _pod(pod_id: str, status: str) -> dict:
    return {"id": pod_id, "name": "aria-v04-train", "desiredStatus": status}


RECORD = {"pod_id": "ydfgy06ik7fzca"}


class TestNeverCreateWhenReuseIsPossible:
    """The operator's instruction, expressed as a decision."""

    def test_stopped_pod_of_record_is_resumed_not_replaced(self):
        decision = por.decide(RECORD, [_pod("ydfgy06ik7fzca", "EXITED")])
        assert decision.action == por.RESUME
        assert decision.pod_id == "ydfgy06ik7fzca"
        assert decision.observed_status == "EXITED"

    def test_running_pod_of_record_is_reused_as_is(self):
        decision = por.decide(RECORD, [_pod("ydfgy06ik7fzca", "RUNNING")])
        assert decision.action == por.REUSE
        assert decision.pod_id == "ydfgy06ik7fzca"

    def test_a_seventy_pod_fleet_yields_one_resume_and_no_create(self):
        """The measured live shape: 69 abandoned pods plus the pod of record."""
        fleet = [_pod(f"abandoned{i:02d}", "EXITED") for i in range(69)]
        fleet.append(_pod("ydfgy06ik7fzca", "EXITED"))
        decision = por.decide(RECORD, fleet)
        assert decision.action == por.RESUME, (
            "a fleet of 70 stopped pods must produce a resume, not a 71st pod"
        )
        assert decision.pod_id == "ydfgy06ik7fzca"


class TestCapacityMovesToAnotherPodWeOwnRatherThanCreating:
    """Measured within an hour of this module shipping: the pod of record's
    host reported `gpuAvailable: 0`, it resumed with no GPU, and refusing left
    a funded balance with nothing able to train. 38 of the 70 pods we already
    own were on hosts with a spare GPU."""

    FLEET = [_pod("ydfgy06ik7fzca", "EXITED"),
             {**_pod("l6o0j96gfqwqui", "EXITED"), "lastStartedAt": "2026-08-19 12:50"},
             {**_pod("older", "EXITED"), "lastStartedAt": "2026-08-01 09:00"}]

    def test_the_pod_of_record_is_kept_when_its_host_can_seat_it(self):
        capacity = {"ydfgy06ik7fzca": 1, "l6o0j96gfqwqui": 5}
        decision = por.decide_with_capacity(RECORD, self.FLEET, capacity)
        assert decision.pod_id == "ydfgy06ik7fzca"
        assert "displaced_pod_of_record" not in decision.evidence

    def test_a_full_host_moves_to_another_pod_we_already_own(self):
        capacity = {"ydfgy06ik7fzca": 0, "l6o0j96gfqwqui": 5, "older": 2}
        decision = por.decide_with_capacity(RECORD, self.FLEET, capacity)
        assert decision.action == por.RESUME
        assert decision.pod_id == "l6o0j96gfqwqui", "most recently used first"
        assert decision.evidence["displaced_pod_of_record"] == "ydfgy06ik7fzca"

    def test_a_full_host_never_escalates_into_creating(self):
        capacity = {"ydfgy06ik7fzca": 0, "l6o0j96gfqwqui": 5}
        assert por.decide_with_capacity(
            RECORD, self.FLEET, capacity).action != por.CREATE

    def test_no_host_anywhere_blocks_rather_than_creating(self):
        capacity = {pod["id"]: 0 for pod in self.FLEET}
        decision = por.decide_with_capacity(RECORD, self.FLEET, capacity)
        assert decision.action == por.BLOCKED
        assert "retry rather than creating" in decision.reason

    def test_unknown_capacity_is_not_treated_as_available(self):
        """None means the provider did not answer. Paying to start on an
        unmeasured host is the guess this module exists to avoid."""
        capacity = {"ydfgy06ik7fzca": 0, "l6o0j96gfqwqui": None, "older": None}
        assert por.decide_with_capacity(
            RECORD, self.FLEET, capacity).action == por.BLOCKED

    def test_an_unreadable_inventory_still_blocks_regardless_of_capacity(self):
        assert por.decide_with_capacity(RECORD, None, {}).action == por.BLOCKED

    def test_a_running_pod_of_record_is_still_reused(self):
        fleet = [_pod("ydfgy06ik7fzca", "RUNNING")]
        assert por.decide_with_capacity(
            RECORD, fleet, {"ydfgy06ik7fzca": 0}).action == por.REUSE


class TestUnreadableIsNeverPermission:
    """'I could not measure' must not become 'nothing exists, go create one'."""

    def test_unreadable_inventory_blocks_rather_than_creates(self):
        decision = por.decide(RECORD, None)
        assert decision.action == por.BLOCKED
        assert decision.action != por.CREATE
        assert "unreadable" in decision.reason

    def test_unrecognised_state_blocks_rather_than_creates(self):
        decision = por.decide(RECORD, [_pod("ydfgy06ik7fzca", "RESTARTING")])
        assert decision.action == por.BLOCKED
        assert decision.observed_status == "RESTARTING"


class TestCreateOnlyWhenGenuinelyGone:
    def test_no_record_registered_authorises_a_create(self):
        decision = por.decide(None, [_pod("someone-elses", "EXITED")])
        assert decision.action == por.CREATE
        assert decision.evidence["fleet_size"] == 1

    def test_pod_absent_from_fleet_authorises_a_create_and_names_the_loss(self):
        decision = por.decide(RECORD, [_pod("other", "EXITED")])
        assert decision.action == por.CREATE
        assert decision.evidence["prior_pod_id"] == "ydfgy06ik7fzca"

    def test_terminated_pod_authorises_a_create(self):
        decision = por.decide(RECORD, [_pod("ydfgy06ik7fzca", "TERMINATED")])
        assert decision.action == por.CREATE


def _delete_verbs(source: str) -> list[str]:
    """Every literal HTTP verb this source can send, that destroys a pod.

    AST, not substring: the module's own docstring says the word "terminate"
    several times explaining why it does not do it, and a guard that reads
    prose reports a paragraph as a defect. Assert the invariant — no DELETE
    reaches the wire — not the spelling.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.strip().upper() == "DELETE":
                found.append(node.value)
        if isinstance(node, ast.keyword) and node.arg == "method":
            value = getattr(node.value, "value", None)
            if isinstance(value, str) and value.strip().upper() == "DELETE":
                found.append(value)
    return found


class TestNothingIsEverDeleted:
    SOURCE = pathlib.Path(por.__file__).read_text(encoding="utf-8")

    def test_module_issues_no_delete(self):
        assert _delete_verbs(self.SOURCE) == [], (
            "R-F4241 forbids destroying a pod; a stopped pod may hold "
            "unharvested measurements"
        )

    def test_the_no_delete_guard_can_actually_fail(self):
        """A guard that cannot fail certifies nothing (R-F3858)."""
        contaminated = self.SOURCE + '\n_req("DELETE", "/pods/x", key)\n'
        assert _delete_verbs(contaminated) == ["DELETE"]

    def test_the_guard_also_catches_a_keyword_method(self):
        contaminated = self.SOURCE + '\nurllib.request.Request(u, method="DELETE")\n'
        assert "DELETE" in _delete_verbs(contaminated)


class TestARunningPodIsNotAUsablePod:
    """Measured 2026-08-23: this pod resumed RUNNING with SSH and NO GPU.

    `runtime.gpus: []` from the provider, and from inside the machine
    `torch.cuda.is_available()` False with no /dev/nvidia*. A launcher handed
    that pod would pip-install and then fail at the CUDA check, billing the
    whole time. 'Started' is not 'can train'.
    """

    def test_the_measured_gpuless_resume_reports_zero_not_unknown(self):
        observed = {"data": {"pod": {"gpuCount": 0, "runtime": {"gpus": []}}}}
        assert por.gpu_count(observed) == 0

    def test_a_healthy_pod_reports_its_gpu(self):
        observed = {"data": {"pod": {"gpuCount": 1, "runtime": {"gpus": [{"id": "0"}]}}}}
        assert por.gpu_count(observed) == 1

    def test_an_unanswered_instrument_is_none_not_zero(self):
        """None and 0 mean different things and must not collapse."""
        assert por.gpu_count(None) is None
        assert por.gpu_count({"errors": [{"message": "timeout"}]}) is None
        assert por.gpu_count({"data": {"pod": None}}) is None

    def test_a_stopped_pod_without_runtime_falls_back_to_the_count(self):
        assert por.gpu_count({"data": {"pod": {"gpuCount": 1}}}) == 1

    def _start(self, monkeypatch, gpus, stopped):
        monkeypatch.setattr(por, "_req", lambda method, path, key, body=None: (
            (200, {}) if path.endswith("/start")
            else (200, {"desiredStatus": "RUNNING", "publicIp": "1.2.3.4",
                        "portMappings": {"22": "22173"}})))
        monkeypatch.setattr(por, "read_gpu_count", lambda pod_id, key: gpus)
        monkeypatch.setattr(por, "stop", lambda pod_id, key: stopped.append(pod_id) or True)
        return por.start_and_wait("ydfgy06ik7fzca", "k", ready_ticks=1)

    def test_a_gpuless_resume_raises_and_stops_the_pod(self, monkeypatch):
        stopped = []
        with pytest.raises(RuntimeError, match="without a usable GPU"):
            self._start(monkeypatch, 0, stopped)
        assert stopped == ["ydfgy06ik7fzca"], (
            "a pod that cannot train must be stopped, not left billing"
        )

    def test_an_unconfirmable_gpu_also_refuses_a_paid_start(self, monkeypatch):
        stopped = []
        with pytest.raises(RuntimeError, match="could not be read"):
            self._start(monkeypatch, None, stopped)
        assert stopped == ["ydfgy06ik7fzca"]

    def test_a_confirmed_gpu_returns_the_connection(self, monkeypatch):
        stopped = []
        result = self._start(monkeypatch, 1, stopped)
        assert result["pod_id"] == "ydfgy06ik7fzca"
        assert result["gpu_count"] == 1
        assert stopped == []

    def test_the_refusal_never_escalates_into_creating_another_pod(self, monkeypatch):
        """The failure mode being fixed: 'my pod has no GPU, make a new one'."""
        stopped = []
        with pytest.raises(RuntimeError) as raised:
            self._start(monkeypatch, 0, stopped)
        assert "not a reason to create another pod" in str(raised.value)


class TestTransientRefusalIsRetriedNotEscalated:
    def test_the_providers_capacity_message_is_transient(self):
        assert por.is_transient_start_failure(
            'HTTP 400: {"error":"not enough free GPUs on host"}')

    def test_an_authentication_refusal_is_not_transient(self):
        assert not por.is_transient_start_failure('HTTP 401: {"error":"unauthorized"}')


class TestAReusedPodCannotPublishAStaleMeasurement:
    """The one hazard reuse introduces, and it is the dangerous kind.

    A fresh pod starts empty, so a harvest can only collect this run's work. A
    reused pod already holds the previous run's reports; a cycle that dies
    before writing its own would let the harvester publish a three-day-old
    report as this run's measurement — on the path that promotes adapters.
    """

    COMMAND = por.archive_command("20260823T060000Z")

    def test_prior_evidence_is_moved_aside_before_work_starts(self):
        assert "mv /workspace/$d /workspace/_prior/20260823T060000Z/$d" in self.COMMAND
        assert "for d in eval logs" in self.COMMAND

    def test_the_completion_sentinel_is_cleared(self):
        """A stale _cycle_status reads as 'this run already succeeded'."""
        assert "rm -f /workspace/eval/_cycle_status" in self.COMMAND

    def test_evidence_is_archived_never_destroyed(self):
        """Operator instruction: nothing is deleted. A superseded report is
        still a measurement that was paid for."""
        destructive = [line for line in self.COMMAND.split(";")
                       if "rm -rf" in line or "rm -r " in line]
        assert destructive == []
        assert "mkdir -p /workspace/_prior/" in self.COMMAND

    def test_checkpoints_are_left_in_place(self):
        """They are already named per run, so they cannot be confused; moving
        1.6 GB every cycle would be cost without a matching risk."""
        assert "checkpoints" not in self.COMMAND

    def test_each_invocation_gets_its_own_archive_so_runs_never_overwrite(self):
        assert por.archive_command("A") != por.archive_command("B")

    def test_the_directories_are_recreated_so_the_cycle_can_write(self):
        assert "mkdir -p /workspace/$d" in self.COMMAND


class TestRecordRoundTrip:
    def test_written_record_is_read_back_and_states_the_policy(self, tmp_path):
        path = tmp_path / "pod_of_record.json"
        written = por.write_record("ydfgy06ik7fzca", "adopted", path=path)
        assert "never DELETE" in written["policy"]
        assert por.read_record(path)["pod_id"] == "ydfgy06ik7fzca"

    def test_a_corrupt_record_reads_as_absent_not_as_a_crash(self, tmp_path):
        path = tmp_path / "pod_of_record.json"
        path.write_text("{not json", encoding="utf-8")
        assert por.read_record(path) is None


@pytest.mark.parametrize("status", ["EXITED", "RUNNING"])
def test_a_live_pod_never_produces_a_create(status):
    assert por.decide(RECORD, [_pod("ydfgy06ik7fzca", status)]).action != por.CREATE
