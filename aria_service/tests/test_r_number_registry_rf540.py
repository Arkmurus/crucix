"""Unit + capability tests for R-F540 reservation log.

Unit: API contract (reserve/peek/mark_shipped/list).
Capability: simulate two-agent collision — the second claimer must NOT
get the same number, the file must round-trip cleanly, and a merge of
two parallel writes must be detectable (i.e. each agent's claim is in
the file after both ship).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from aria_service.intel import r_number_registry as reg


@pytest.fixture
def tmp_log(tmp_path: Path) -> Path:
    p = tmp_path / "r_number_reservations.json"
    p.write_text(json.dumps({
        "schema_version": 1,
        "next_available": 600,
        "reservations": [],
    }), encoding="utf-8")
    return p


def test_reserve_returns_next_r_number(tmp_log: Path) -> None:
    r = reg.reserve("first claim", agent="t1", path=tmp_log)
    assert r == "R-F600"
    data = json.loads(tmp_log.read_text(encoding="utf-8"))
    assert data["next_available"] == 601
    assert len(data["reservations"]) == 1
    assert data["reservations"][0]["claimed_by"] == "t1"
    assert data["reservations"][0]["status"] == "in_progress"


def test_reserve_increments_across_claims(tmp_log: Path) -> None:
    a = reg.reserve("a", agent="t1", path=tmp_log)
    b = reg.reserve("b", agent="t2", path=tmp_log)
    c = reg.reserve("c", agent="t1", path=tmp_log)
    assert (a, b, c) == ("R-F600", "R-F601", "R-F602")


def test_peek_does_not_claim(tmp_log: Path) -> None:
    n1 = reg.peek_next(path=tmp_log)
    n2 = reg.peek_next(path=tmp_log)
    assert n1 == n2 == "R-F600"
    data = json.loads(tmp_log.read_text(encoding="utf-8"))
    assert data["reservations"] == []


def test_mark_shipped_stamps_sha(tmp_log: Path) -> None:
    r = reg.reserve("ship me", agent="t1", path=tmp_log)
    reg.mark_shipped(r, "abc1234", path=tmp_log)
    rec = reg.list_reservations(path=tmp_log)[0]
    assert rec["status"] == "shipped"
    assert rec["commit_sha"] == "abc1234"
    assert "shipped_at" in rec


def test_mark_shipped_unknown_raises(tmp_log: Path) -> None:
    with pytest.raises(KeyError):
        reg.mark_shipped("R-F999", "abc", path=tmp_log)


def test_mark_shipped_rejects_bad_format(tmp_log: Path) -> None:
    with pytest.raises(ValueError):
        reg.mark_shipped("not-an-r-number", "abc", path=tmp_log)


def test_mark_abandoned_records_reason(tmp_log: Path) -> None:
    r = reg.reserve("doomed", agent="t1", path=tmp_log)
    reg.mark_abandoned(r, "superseded by sibling work", path=tmp_log)
    rec = reg.list_reservations(status_filter="abandoned", path=tmp_log)[0]
    assert rec["abandon_reason"] == "superseded by sibling work"


def test_list_reservations_status_filter(tmp_log: Path) -> None:
    a = reg.reserve("a", agent="t1", path=tmp_log)
    b = reg.reserve("b", agent="t1", path=tmp_log)
    reg.mark_shipped(a, "sha1", path=tmp_log)
    shipped = reg.list_reservations(status_filter="shipped", path=tmp_log)
    pending = reg.list_reservations(status_filter="in_progress", path=tmp_log)
    assert {r["r_number"] for r in shipped} == {a}
    assert {r["r_number"] for r in pending} == {b}


def test_reserve_skips_already_reserved_numbers(tmp_path: Path) -> None:
    """If next_available drifts behind existing claims, reserve catches up."""
    p = tmp_path / "drift.json"
    p.write_text(json.dumps({
        "schema_version": 1,
        "next_available": 600,
        "reservations": [{
            "r_number": "R-F600",
            "title": "pre-existing",
            "claimed_at": "2026-01-01T00:00:00Z",
            "claimed_by": "other",
            "status": "shipped",
            "commit_sha": "deadbeef",
        }],
    }), encoding="utf-8")
    r = reg.reserve("after drift", agent="t1", path=p)
    assert r == "R-F601"


def test_reserve_rejects_empty_title(tmp_log: Path) -> None:
    with pytest.raises(ValueError):
        reg.reserve("   ", agent="t1", path=tmp_log)


def test_concurrent_reserve_no_dup(tmp_log: Path) -> None:
    """Capability: 8 threads racing reserve() must produce 8 unique R-numbers."""
    results: list[str] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        r = reg.reserve(f"thread {i}", agent=f"t{i}", path=tmp_log)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    assert len(set(results)) == 8, f"collision detected: {results}"


def test_real_log_loads_and_passes_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Capability: the real shipped data/r_number_reservations.json must
    parse, have monotonic next_available > max(reservation numbers), and
    contain no duplicate r_numbers."""
    repo_path = Path(__file__).resolve().parents[2] / "data" / "r_number_reservations.json"
    assert repo_path.exists(), "shipped reservation log missing"
    data = json.loads(repo_path.read_text(encoding="utf-8"))
    assert data.get("schema_version") == 1
    assert "next_available" in data
    nums = [int(r["r_number"].split("R-F")[1]) for r in data["reservations"]]
    assert len(nums) == len(set(nums)), "duplicate r_numbers in shipped log"
    if nums:
        assert data["next_available"] > max(nums)
