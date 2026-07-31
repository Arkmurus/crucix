"""R-F3533 — recover the vectors instead of recomputing them, and make the rebuild resume.

Both defects were found by DOING the recovery on 2026-07-31 (`aria_facts`, 489,002
records) and hitting them.

DEFECT 1 — the tool re-embeds when the vectors are intact. A chromadb collection whose
HNSW is corrupt has NOT lost its data:

    data_level0.bin     817,499,168 B  INTACT  (the vectors)
    link_lists.bin   82,140,554,932 B  CORRUPT (82GB apparent in a 3.7GB store)

hnswlib reads link_lists.bin at load and segfaults on that length. Re-embedding all
489,002 measured ~13 docs/sec = NINE HOURS. Recovering the vectors took ~25 minutes and
is byte-exact, where re-embedding is only *equivalent*.

DEFECT 2 — the tool cannot resume. `rebuild()` names its staging collection with a fresh
timestamp, so a killed run restarts from ZERO. The box restarted three times during that
one rebuild (twice under load, once a peer's deploy). A 9-hour job would never have
finished; with resume, the peer's deploy cost four minutes.

THE TRAP THIS FILE EXISTS TO PIN. Deriving the stride arithmetically is NOT enough:
817,499,168 factorises EXACTLY as both 1672 x 489,002 and 1676 x 487,768, and element 0
verifies under BOTH. I shipped the wrong one first and got garbage labels for every other
element. So `hnsw_layout` returns CANDIDATES and `recover_vectors` refuses any candidate
its `verify_fn` cannot confirm — a misparse silently writes wrong vectors into the
knowledge base, which is worse than a slow rebuild.
"""
from __future__ import annotations

import importlib.util
import pathlib
import struct

import numpy as np
import pytest

_TOOL = (pathlib.Path(__file__).resolve().parents[2]
         / "scripts" / "admin" / "rebuild_rag_collection.py")
_spec = importlib.util.spec_from_file_location("rebuild_rag_collection", _TOOL)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

DIM = 384


def _write_segment(tmp_path, *, stride, off_data, off_label, label_size, n, vectors):
    """Build a data_level0.bin with a chosen layout, so the parser is tested against a
    file whose ground truth we know."""
    buf = bytearray(stride * n)
    for i in range(n):
        base = i * stride
        buf[base + off_data: base + off_data + DIM * 4] = vectors[i].tobytes()
        fmt = "<I" if label_size == 4 else "<Q"
        struct.pack_into(fmt, buf, base + off_label, i + 1)
    seg = tmp_path / "seg"
    seg.mkdir(exist_ok=True)
    (seg / "data_level0.bin").write_bytes(bytes(buf))
    return str(seg)


@pytest.fixture
def real_layout(tmp_path):
    """The layout confirmed live: stride 1676, vector @132, uint32 label @1668."""
    n = 40
    rng = np.random.default_rng(31)
    vecs = rng.standard_normal((n, DIM)).astype(np.float32)
    seg = _write_segment(tmp_path, stride=1676, off_data=132, off_label=1668,
                         label_size=4, n=n, vectors=vecs)
    label_to_id = {i + 1: f"id-{i}" for i in range(n)}
    return seg, label_to_id, vecs, n


# ── recovery ────────────────────────────────────────────────────────────────

def test_capability_vectors_are_recovered_byte_exactly(real_layout):
    seg, label_to_id, vecs, n = real_layout
    layout = tool.hnsw_layout(seg, n_records=n, dim=DIM)
    assert layout, "no candidate layout derived"

    res = tool.recover_vectors(seg, label_to_id, layout,
                               verify_fn=lambda cid, v: True, dim=DIM)
    assert res["stride"] == 1676
    assert len(res["vectors"]) == n
    for i in range(n):
        assert np.array_equal(res["vectors"][f"id-{i}"], vecs[i]), (
            "recovered vector is not byte-identical — this is a REPLACEMENT, not a "
            "recovery, and the whole advantage over re-embedding is gone")


def test_the_real_layout_is_among_the_candidates(real_layout):
    seg, _, _, n = real_layout
    layout = tool.hnsw_layout(seg, n_records=n, dim=DIM)
    strides = [c["stride"] for c in layout["candidates"]]
    assert 1676 in strides, f"the live-confirmed stride is not offered: {strides}"


def test_an_unverifiable_candidate_is_REJECTED(real_layout):
    """THE SAFETY PROPERTY. A stride that merely divides the file exactly is not proof;
    817,499,168 divided exactly two different ways and element 0 passed under both.
    If nothing can be verified, recovery must yield NOTHING rather than plausible junk.
    """
    seg, label_to_id, _, n = real_layout
    layout = tool.hnsw_layout(seg, n_records=n, dim=DIM)
    res = tool.recover_vectors(seg, label_to_id, layout,
                               verify_fn=lambda cid, v: False, dim=DIM)
    assert res["vectors"] == {}, "an unverified parse was accepted"
    assert res["stride"] is None


def test_verification_selects_the_CORRECT_candidate_when_several_fit(tmp_path):
    """Reproduces the ambiguity that cost two failed attempts.

    In production 817,499,168 divided EXACTLY by both 1672 and 1676, and element 0
    verified under both — so arithmetic alone chose wrong. To recreate that here the
    file must be divisible by two candidate strides, which needs a size that is a common
    multiple; a small arbitrary n yields only one fit and would make this vacuous.
    """
    # lcm(1672, 1676) = 700,568 bytes = 418 records at stride 1676, and exactly 419 at
    # 1672 — so BOTH are exact divisors, which is the production situation.
    n = 418
    rng = np.random.default_rng(7)
    vecs = rng.standard_normal((n, DIM)).astype(np.float32)
    seg = _write_segment(tmp_path, stride=1676, off_data=132, off_label=1668,
                         label_size=4, n=n, vectors=vecs)
    label_to_id = {i + 1: f"id-{i}" for i in range(n)}
    layout = tool.hnsw_layout(seg, n_records=n, dim=DIM)
    strides = {c["stride"] for c in layout["candidates"]}
    assert len(strides) > 1, (
        f"test is vacuous unless several strides fit; got {strides}")

    # Only accept a vector that genuinely matches the known ground truth.
    def verify(cid, v):
        return np.array_equal(v, vecs[int(cid.split("-")[1])])

    res = tool.recover_vectors(seg, label_to_id, layout, verify_fn=verify, dim=DIM)
    assert res["stride"] == 1676, (
        f"verification picked the wrong candidate: {res['stride']}")
    assert len(res["vectors"]) == n


def test_a_missing_segment_file_is_not_an_exception(tmp_path):
    assert tool.hnsw_layout(str(tmp_path), n_records=10, dim=DIM) is None


# ── resume ──────────────────────────────────────────────────────────────────

class _Col:
    def __init__(self, ids=None, raises=False):
        self._ids = list(ids or [])
        self._raises = raises

    def get(self, include=None):
        if self._raises:
            raise RuntimeError("segment unreadable")
        return {"ids": list(self._ids)}


def test_capability_resume_reports_what_is_already_written():
    assert tool.staging_existing_ids(_Col(["a", "b", "c"])) == {"a", "b", "c"}


def test_an_unreadable_staging_starts_fresh_rather_than_crashing():
    """A corrupt/absent staging must not abort the rebuild — restarting from zero is
    recoverable; refusing to run at all is not."""
    assert tool.staging_existing_ids(_Col(raises=True)) == set()


def test_empty_staging_is_an_empty_set_not_None():
    assert tool.staging_existing_ids(_Col([])) == set()


# ── the tool still documents its own contract ───────────────────────────────

def test_the_tool_records_why_recovery_beats_re_embedding():
    src = _TOOL.read_text(encoding="utf-8", errors="replace")
    assert "link_lists.bin" in src, "the actual corruption is not named"
    assert "byte-exact" in src or "byte-identical" in src
    assert "ARITHMETIC IS NOT PROOF" in src, (
        "the trap that cost two attempts must stay written down")


def test_never_deletes_remains_the_contract():
    src = _TOOL.read_text(encoding="utf-8", errors="replace")
    assert "NEVER DELETES" in src
    assert "RENAMED" in src
