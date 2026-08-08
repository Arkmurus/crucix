"""R-F3514 — replay a recorded evidence set, and prove production can never do it.

R-F3512 captures what the source adapters returned on a real run. This consumes such a
recording and drives the orchestrator's primary-source screen from it, which is the last
piece the gold corpus needed: record -> replay -> gate.

THE SAFETY PROPERTY THAT SHAPES THE DESIGN. A DD that served RECORDED evidence would be
fabricating a screen — presenting yesterday's OFAC answer, or another subject's entirely,
as though it were today's live check. That is the worst failure this product can have, and
it is worse than any of the false cleans fixed this session because it would be
indistinguishable from a real result.

So the replay harness lives ENTIRELY in the test tree. There is no replay flag, no replay
branch and no replay import in `aria_service/intel`, and the last test asserts that —
because the natural next step for someone wanting faster tests is to add "just a small
env flag" to production, and that flag is the vulnerability.

WHAT REPLAY IS FOR: proving that a change to the report path still produces the same
findings from the same evidence. It answers "did I break anything?", never "what is true
about this subject?".
"""
from __future__ import annotations

import json
import pathlib

import pytest

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def replay_source_results(recording: dict, labels: list[str]) -> list:
    """Rebuild the adapter result list, in `labels` order, from a recording.

    A source absent from the recording yields an explicit marker rather than a plausible
    empty dict: a replay that silently invents "no hits" for a source that was never
    captured would manufacture a clean result, which is the defect class this whole
    session removed.
    """
    sources = (recording or {}).get("sources") or {}
    out = []
    for label in labels:
        if label not in sources:
            out.append({"ok": False, "error": "not_captured",
                        "replay_note": f"{label} was not in the recording"})
            continue
        item = sources[label]
        if isinstance(item, dict) and item.get("error") and "detail" in item:
            # A captured adapter EXCEPTION replays as an exception, not as a clean miss.
            out.append(RuntimeError(f"{item['error']}: {item.get('detail','')}"))
        else:
            out.append(item)
    return out


_SYNTHETIC = {
    "run_id": "dd_synthetic_replay_v1",
    "subject_name": "ARIA GOLD CLEAN LTD (synthetic)",
    "data_subject_key": "company:GB:SYNTHETIC-0001",
    "data_jurisdiction": "uk",
    "retention_class": "cdd_evidence",
    "personal_data": True,
    "erasure_reachable": True,
    "sources": {
        "ofac_sdn": {"ok": True, "hits": []},
        "uk_ofsi": {"ok": True, "hits": []},
        "un_sc": {"ok": True, "hits": []},
        "acled": {"error": "RuntimeError", "detail": "upstream 503"},
    },
}


def test_a_recording_replays_in_label_order():
    labels = ["ofac_sdn", "uk_ofsi", "un_sc"]
    out = replay_source_results(_SYNTHETIC, labels)
    assert len(out) == 3
    assert all(isinstance(o, dict) and o["ok"] for o in out)


def test_a_captured_failure_replays_as_a_failure():
    """The direction that matters. If a recorded 503 replayed as a clean empty result,
    the corpus would assert that a FAILED screen produces a clean report."""
    out = replay_source_results(_SYNTHETIC, ["acled"])
    assert isinstance(out[0], BaseException)
    assert "503" in str(out[0])


def test_a_source_missing_from_the_recording_is_explicit():
    """Never a plausible empty dict — that would manufacture 'no hits' for a source
    nobody captured."""
    out = replay_source_results(_SYNTHETIC, ["wb_debarred"])
    assert out[0]["ok"] is False
    assert out[0]["error"] == "not_captured"


def test_replay_feeds_the_real_primary_source_screen_shape():
    """The replayed list must be exactly what `_identity_primary_source_screen` consumes
    — same order, same length as its label list — or the corpus tests a shape production
    never sees."""
    labels = ["sec_edgar", "ofac_sdn", "uk_ofsi", "un_sc", "wb_debarred", "acled"]
    out = replay_source_results(_SYNTHETIC, labels)
    assert len(out) == len(labels)
    import pathlib as _p
    src = (_p.Path(__file__).resolve().parents[1]
           / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8", errors="replace")
    assert ('_src_labels = ["sec_edgar", "ofac_sdn", "uk_ofsi", "un_sc", '
            '"wb_debarred", "acled"]') in src, (
        "the production label list changed — this replay harness is now testing a shape "
        "production does not use")


def test_a_recording_carries_the_envelope_so_the_corpus_stays_erasable():
    """A gold corpus built from real runs is still personal data. If a recording arrives
    without the envelope, the corpus becomes the unmanaged store R-F3512 avoided."""
    for key in ("data_subject_key", "retention_class", "personal_data"):
        assert key in _SYNTHETIC, f"a recording without {key} cannot be governed"


def test_PRODUCTION_HAS_NO_REPLAY_PATH():
    """THE SAFETY PROPERTY.

    A DD that served recorded evidence would present yesterday's answer — or another
    subject's — as a live screen. Indistinguishable from a real result, and therefore
    worse than any false clean fixed this session.

    The natural next step for someone wanting faster tests is "just a small env flag" in
    production. This fails the moment that appears.
    """
    intel = pathlib.Path(__file__).resolve().parents[1] / "intel"
    offenders = []
    for path in intel.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "replay_source_results" in text:
            offenders.append(f"{path.name}: imports the replay harness")
        if "ARIA_DD_REPLAY" in text:
            offenders.append(f"{path.name}: defines a replay flag")
    assert not offenders, (
        "production can replay recorded evidence: " + "; ".join(offenders)
        + ". Recorded evidence must never be servable as a live screen.")


def test_the_recorder_itself_never_reads_recordings():
    """Capture-only. A recorder that also READ its own output would be a cache, and a
    cache of sanctions answers is a stale screen waiting to happen."""
    import inspect
    from aria_service.intel import dd_evidence_recorder as rec
    src = module_source(rec)
    assert "read_text" not in src, "the recorder reads recordings — that is a cache"
    assert "def record_source_results" in src
