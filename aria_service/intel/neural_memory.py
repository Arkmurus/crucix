"""ARIA Neural Memory — Associativ

e Knowledge Graph with Growing Neurons.

Each "neuron" is a concept node with:
  - Connections to other neurons (weighted edges)
  - Activation strength (increases with use, decays with time)
  - Confidence level (grows as evidence accumulates)
  - Source attributions (where ARIA learned this)

The network grows autonomously:
  - Every conversation extracts concepts and links them
  - Frequently co-occurring concepts form stronger connections
  - Unused neurons decay (but never fully die — long-term memory)
  - ARIA can recall by association: ask about "Angola" and she pulls
    connected neurons like "FADM", "Luanda procurement", "Baykar UAV"

This gives ARIA emergent intelligence — she doesn't just search keywords,
she thinks in connected concepts like a human analyst."""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import asyncio
import base64
import gc
import gzip
import json
import logging
import math
import os
import orjson  # R-F2081 — C-speed JSON encoder (in image; verified live); ~4x faster than stdlib json on the edges graph
import re
import time
import uuid
import zlib  # R-F2082 — stable crc32 shard hashing for edges
from collections import defaultdict
from typing import Any, Optional

from . import redis_store as rs
# R-F4173 (C-185) — bind the exception CLASS directly rather than reaching for
# it through the `rs` module attribute at raise/except time. Tests (and any
# future caller) swap the `rs` module object wholesale, and an attribute lookup
# on the swapped object turns that into an AttributeError inside the boot path.
# The class itself never moves.
from .redis_store import StoreReadError
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.neural")

# ── Constants ────────────────────────────────────────────────────────────────
NEURONS_KEY = "crucix:aria:neurons"
EDGES_KEY = "crucix:aria:neural_edges"
NEURAL_META_KEY = "crucix:aria:neural_meta"

# R-F699 (2026-05-18) — shard the neurons blob to stop the 4MB-per-absorb
# write that was tripping the brain_hook breaker (live logs 2026-05-18
# 17:59:40 + 18:37:07: PR04 cascades right after `Redis SET neurons:
# value size 4025697 bytes exceeds warn threshold`). Same pattern R-F334
# applied to knowledge.py — N shards × ~500KB each, plus a meta key
# carrying the total-neuron count for the R-F371 disk-check.
#
# Migration: first boot after deploy reads legacy NEURONS_KEY (still
# present in Redis), populates memory, and on first _persist() writes
# the sharded form. Subsequent boots read sharded. Legacy key is left
# in place (no destructive cleanup) — operator can DEL after a clean
# week or it'll TTL eventually depending on the backend policy.
NEURONS_SHARD_META_KEY = "crucix:aria:neurons:meta"
NEURONS_SHARD_KEY_FMT = "crucix:aria:neurons:shard:{i}"
NEURONS_SHARD_TARGET_BYTES = 500_000
NEURONS_SHARD_MAX_PER_SHARD = 3000   # hard cap on neurons per shard

# R-F2082 (2026-06-28) — SHARD THE EDGES GRAPH. The edges blob grew to ~70 MB
# uncompressed / ~12 MB gzipped (2.4M edges across 22k groups) and was re-encoded
# WHOLE on every dirty persist — a 7-8s GIL-bound json+gzip that wedged the single
# event loop every few minutes (the brain-side root cause of slow web population;
# R-F2081 cut the constant, this eliminates the CLASS). Edges are now split across
# a FIXED number of shards by a stable crc32 of the group key, and each persist
# re-encodes ONLY the shards whose edges changed since the last write — so the
# per-persist CPU is O(changed shards), bounded regardless of how large the
# infinite-memory graph grows. Each shard encodes in its own throttled worker call
# with the loop free between, so even a full migration never wedges. "If size is
# required, scale it": raise ARIA_NEURAL_EDGES_SHARDS to keep per-shard work small
# as the graph grows (more shards → smaller chunks). Mirrors the R-F699 neuron
# sharding (legacy single-blob fallback on read; migrates on first persist).
EDGES_SHARD_META_KEY = "crucix:aria:neural_edges:meta"
EDGES_SHARD_KEY_FMT = "crucix:aria:neural_edges:shard:{i}"
EDGES_SHARD_COUNT = max(8, min(256, int(os.getenv("ARIA_NEURAL_EDGES_SHARDS", "32"))))

# R-F240 (2026-05-11) — WARN thresholds, not hard caps.
# Per the "ARIA has infinite memory" rule (memory/aria_infinite_memory.md),
# neurons and edges persist forever. Activation DECAY is fine (relevance
# weighting fades with disuse — that's how the brain ranks recall), but
# DELETION of weak neurons / edges is forbidden. Old neurons stay at
# MIN_ACTIVATION; old edges stay at MIN_EDGE_WEIGHT — they're at the
# bottom of any retrieval ranking but they still exist for forensic
# recall ("when did ARIA first hear about X?").
MAX_NEURONS = 100_000_000          # was 50K with prune; now warn-only at 100M
MAX_EDGES_PER_NEURON = 100_000     # was 200 with prune; now warn-only at 100K

# R-F241 (2026-05-11) — env-var parse hardening. Pre-R-F241 a malformed
# env value like ARIA_NEURAL_WARN_NEURONS=foo would crash module import
# (verification agent flagged this). Now we fall back to the default
# if the env value isn't a valid int.
def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        # Logger isn't ready at module-load time — print to stderr
        # so the operator sees the misconfig at boot.
        import sys
        sys.stderr.write(
            f"[neural_memory] R-F241: {name}={raw!r} is not a valid int; "
            f"falling back to default {default}\n"
        )
        return default

WARN_NEURONS = _env_int("ARIA_NEURAL_WARN_NEURONS", 200000)
WARN_EDGES_PER_NEURON = _env_int("ARIA_NEURAL_WARN_EDGES_PER_NEURON", 2000)
# R-F1512: soft cap on edges per neuron. When a neuron exceeds this, its
# weakest edges are offloaded to cold storage (a separate JSON key) instead
# of being kept in the hot edges dict. This prevents unbounded graph growth
# from degrading neural tier performance (the >3.5s timeout seen in prod).
# The cold edges are still queryable — they're just not loaded into memory
# on every absorb cycle. They're reloaded when the neuron is specifically
# activated.
_MAX_HOT_EDGES_PER_NEURON = _env_int("ARIA_NEURAL_MAX_HOT_EDGES", 5000)
# R-F2102 — BOUND recall()'s spreading-activation BFS. A neuron can have up to
# _MAX_HOT_EDGES_PER_NEURON (5000) edges, so an UNBOUNDED depth-2 spread from 10
# seeds could touch 10×5000=50k nodes then 50k×5000=250M edge-ops — seconds of
# GIL-bound work that ran on the event loop (via _prefetch_neural) and wedged the
# WHOLE chat turn + every document review (live wedge_681). These caps make recall
# O(bounded) regardless of graph density; the neural layer is supplementary context
# so a bounded approximation is fine. Env-tunable.
_RECALL_MAX_CANDIDATES = _env_int("ARIA_NEURAL_RECALL_MAX_CANDIDATES", 4000)
_RECALL_MAX_NODES = _env_int("ARIA_NEURAL_RECALL_MAX_NODES", 1200)
_RECALL_MAX_EDGES_PER_NODE = _env_int("ARIA_NEURAL_RECALL_MAX_EDGES_PER_NODE", 64)
_COLD_EDGES_KEY_PREFIX = "crucix:aria:neural_cold_edges:"
MIN_EDGE_WEIGHT = 0.001            # floor — edges decay TO this, not below
_neural_warn_throttle = 0
DECAY_RATE = 0.997          # per-day decay (0.3% per day — memories last longer)
MIN_ACTIVATION = 0.05       # neurons never fully die
ACTIVATION_BOOST = 0.2      # each access boosts activation (faster learning)
CO_OCCURRENCE_BOOST = 0.12  # co-occurring concepts strengthen edges (stronger associations)
NEW_NEURON_ACTIVATION = 0.5
PRUNE_THRESHOLD = 0.08      # prune edges weaker than this

# ── In-memory cache ──────────────────────────────────────────────────────────
_neurons: dict[str, dict] = {}       # id → neuron
_edges: dict[str, dict[str, float]] = defaultdict(dict)  # from_id → {to_id: weight}
_meta: dict = {"total_neurons": 0, "total_edges": 0, "total_activations": 0, "born": None}
_loaded = False

# ── R-F4173 (C-185) — "LOADED" MEANT "WE STOPPED TRYING" ────────────────────
#
# `_loaded` is set True on the success path AND in init()'s except branch, so it
# cannot answer "is the graph actually here?". R-F2951's boot guard reads it and
# was therefore blind on 2026-08-19, when a store read TIMEOUT during boot made
# init() adopt an empty edge set: `_loaded` True, `total_edges` 0, and R-F251
# reported `neural_edges: 159198 -> 0 (-100%)` on a graph that had lost nothing
# (the same process read 159,254 edges twenty minutes later).
#
# `_load_complete` is the honest one: True ONLY when every store read for the
# graph succeeded. `_loaded` keeps its existing meaning for the callers that
# use it as an "init has run" latch (e.g. detect_conflict), so this adds a fact
# rather than changing one.
_load_complete = False

# R-F1932 (G4 tail): inverted indices so _find_neuron + recall()'s seed scan are
# O(matching neurons), not O(all neurons). _neurons grows forever (infinite memory),
# so the old per-call full scans were a creeping on-event-loop wedge (audit H4).
# `concept` is stored lowercased + is immutable, and neurons are never deleted, so
# these indices only ever GROW — no per-neuron removal needed.
_concept_to_id: dict[str, str] = {}   # concept(lower) -> neuron id   (exact lookup)
_word_to_ids: dict[str, set] = {}     # concept word   -> {neuron ids} (overlap candidates)


def _index_neuron_into(n: dict, concept_to_id: dict, word_to_ids: dict) -> None:
    """R-F2200 — index one neuron into the PASSED indices (not the module
    globals), so a fresh index can be built off to the side and swapped in."""
    nid = n.get("id")
    if not nid:
        return
    concept = n.get("concept") or ""
    concept_to_id[concept] = nid
    for w in concept.split():
        word_to_ids.setdefault(w, set()).add(nid)


def _index_neuron(n: dict) -> None:
    """Add one neuron to the inverted indices (idempotent)."""
    _index_neuron_into(n, _concept_to_id, _word_to_ids)


def _rebuild_neuron_index() -> None:
    """Rebuild both indices from _neurons — call after any BULK load/replace.
    SYNCHRONOUS + GIL-bound. For the BOOT/warmup path use the incremental async
    variant below (this stays for small, non-loop-critical rebuilds)."""
    _concept_to_id.clear()
    _word_to_ids.clear()
    for n in _neurons.values():
        _index_neuron(n)


async def _rebuild_neuron_index_incremental(chunk: int = 5000) -> None:
    """R-F2200 — WARMUP OFF-LOAD: rebuild the neuron indices INCREMENTALLY,
    yielding to the event loop every `chunk` neurons, then SWAP the globals
    atomically.

    Why not to_thread: building the index is pure-Python (dict inserts over
    ~1.2M neurons) and therefore GIL-bound, so a worker thread would hold the
    GIL the whole time and starve the loop just the same (verified pattern,
    R-F2144). Chunked `await asyncio.sleep(0)` yields are the only thing that
    keeps the loop responsive during the boot warmup — the live event-loop
    stalls (8.8s) traced here.

    Why a fresh-build + swap (not clear-then-fill the live dicts): the sync
    version `.clear()`s the live indices first, briefly exposing an EMPTY index
    to concurrent readers (a recall mid-rebuild would miss everything). Building
    into fresh dicts and swapping the globals in ONE assignment each means
    readers see the OLD complete index until the instant swap — no empty window,
    no concurrent-read race."""
    global _concept_to_id, _word_to_ids
    new_concept: dict = {}
    new_word: dict = {}
    count = 0
    for neuron in list(_neurons.values()):
        _index_neuron_into(neuron, new_concept, new_word)
        count += 1
        if count % max(1, chunk) == 0:
            await asyncio.sleep(0)  # let the event loop breathe
    _concept_to_id = new_concept
    _word_to_ids = new_word

# R-F442 (2026-05-13) — write-skipping dirty flag for the edges blob.
# Pre-R-F442 _persist() unconditionally rewrote EDGES_KEY (~4.14 MB) on
# every call, including from recall(), which only mutates neuron-level
# fields (activation, last_activated) and never touches _edges. Live fly
# logs 2026-05-13 21:07 showed 8 redundant 4.14 MB writes per minute,
# all tripping the 4 MB Redis warn threshold. With dirty tracking, only
# the 3 sites that actually mutate edges (_strengthen_edge, _apply_decay,
# init-disk-reload) reset the flag. Default True so the FIRST persist
# after boot always syncs disk → Redis.
_edges_dirty: bool = True

# R-F2082 — per-shard dirty tracking for the sharded edges write. `_edges_dirty`
# stays the cheap "anything changed at all" gate (skip the whole edges save when
# False); this set records WHICH shards changed so _persist re-encodes only those.
# `_edges_sharded_initialized` is False until we've written the sharded form once
# (forces a full shard write on the first persist after a legacy-blob boot).
_dirty_edge_shards: set[int] = set()
_edges_sharded_initialized: bool = False

# R-F986 (2026-05-28) — debounce window for _maybe_persist() (see below). The
# high-frequency callers (learn_from_text = the absorb storm, recall = the chat
# retrieval path) re-gzip the full neuron set on every call via _persist(), a
# GIL-bound CPU burst that piled onto the event loop and slowed chats (143s
# traces / R-F704 wedges). _maybe_persist() coalesces them on this interval.
_last_neurons_write: float = 0.0
_NEURONS_MIN_WRITE_INTERVAL_S = float(
    os.getenv("ARIA_NEURAL_NEURONS_FLUSH_S", "20") or "20"
)

# R-F442 (2026-05-13) — gzip wrapper for the edges blob. Same pattern as
# R-F1 (knowledge) and R-F36 (intel_ledger). 4.14 MB raw JSON compresses
# to ~700 KB (sparse-graph + repeated float patterns), well under the
# 4 MB warn threshold with multi-month headroom. Magic prefix lets the
# loader accept both gzipped (new) and raw-JSON (legacy) blobs so
# existing Redis snapshots migrate forward on the next write.
_GZ_PREFIX = "GZ1:"
# R-F2081 (2026-06-28) — the edges graph is ~70 MB uncompressed / ~12 MB gzipped
# in prod. Re-encoding the WHOLE blob on every dirty persist wedged the single
# event loop for 7-8s (live wedge_677: heartbeat stale 8.40s/6.72s/7.61s, top
# frame _encode_edges → json.dumps), every few minutes — the brain-side root
# cause of "web slow to populate" (every dashboard/chat request stalls behind the
# wedge). Even in a worker thread, json.dumps + gzip(level 6) of a 70 MB blob
# holds the GIL the whole time, starving the loop. Measured on a prod-matched
# payload: json+gzip6=4243ms (→7-8s under prod contention) vs orjson+gzip1=645ms
# (~1.3s prod) — 6.6x, well under the 5s wedge threshold. gzip 1 costs ~+6 MB of
# storage (12→19 MB) against a 1 GB DB — negligible. Env-overridable. The PERMANENT
# fix (bounded work regardless of infinite-memory growth) is sharding the edges
# like neurons (R-F699) — tracked as R-F2082.
_EDGES_GZIP_LEVEL = max(1, min(9, int(os.getenv("ARIA_NEURAL_EDGES_GZIP_LEVEL", "1"))))


def _encode_edges(edges_dict: dict) -> str:
    """R-F442: gzip+base64-encode the edges blob for Redis storage.

    R-F727 (2026-05-19): drop `default=str` on the fast path. The
    C-accelerated `_json` encoder is bypassed whenever `default=` is
    passed, forcing CPython's pure-Python JSONEncoder which holds the
    GIL across the entire iteration. Live wedge stack (wedge_673,
    213.97s loop stall) had 3 worker threads simultaneously in
    `json/encoder.py iterencode` + `gzip.compress` here, each
    holding the GIL through its Python frames; the main loop
    starved. `_edges` is structurally `{str: {str: float}}` so
    `default` is a defensive no-op. Try fast path first; fall back
    to default=str on the impossible TypeError so a non-native type
    leaking in does NOT crash persist."""
    # R-F1639 — disable cyclic GC across the dump+gzip (same root + fix as
    # R-F1621 for knowledge.py): serialising the edges graph allocates a flood
    # of temp objects that trip gen0/1 collections, which also re-scan the large
    # long-lived neural graph in older generations — holding the GIL in this
    # throttled worker thread and starving the asyncio loop (wedge_675,
    # neural_memory:164). gc.freeze() at boot covers the boot graph; this covers
    # the per-encode churn. Restored in finally; never enables GC a caller had off.
    _gc_was_enabled = gc.isenabled()
    if _gc_was_enabled:
        gc.disable()
    try:
        # R-F2081 — orjson (C, ~4x stdlib json) + gzip level 1. orjson.dumps
        # returns bytes directly (no .encode). The except path mirrors the old
        # default=str defence; OPT_NON_STR_KEYS keeps orjson as lenient as
        # json.dumps was on a non-str key leaking in (orjson is strict by default).
        try:
            raw = orjson.dumps(edges_dict)
        except (TypeError, orjson.JSONEncodeError):
            raw = orjson.dumps(edges_dict, default=str, option=orjson.OPT_NON_STR_KEYS)
        gz = gzip.compress(raw, compresslevel=_EDGES_GZIP_LEVEL)
        return _GZ_PREFIX + base64.b64encode(gz).decode("ascii")
    finally:
        if _gc_was_enabled:
            gc.enable()


def _decode_edges(value: Any) -> dict | None:
    """R-F442: decode an edges blob. Accepts both gzipped (`GZ1:` prefix)
    and legacy raw-JSON values, so an existing 4 MB raw snapshot loads
    fine on the first boot after deploy."""
    if not value:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except Exception:
            return None
    if not isinstance(value, str):
        return None
    if value.startswith(_GZ_PREFIX):
        try:
            gz = base64.b64decode(value[len(_GZ_PREFIX):])
            raw = gzip.decompress(gz)
            return json.loads(raw)
        except Exception as e:
            logger.warning("R-F442: gzip edges decode failed: %s", e)
            return None
    try:
        return json.loads(value)
    except Exception:
        return None


# ── Core Data Structures ────────────────────────────────────────────────────
def _make_neuron(concept: str, category: str = "general", source: str = "auto",
                 confidence: str = "ASSESSED") -> dict:
    now = time.time()
    return {
        "id": str(uuid.uuid4())[:12],
        "concept": concept.strip().lower(),
        "label": concept.strip(),  # human-readable (preserves case)
        "category": category,      # market, oem, person, capability, event, regulation, general
        "activation": NEW_NEURON_ACTIVATION,
        "confidence": confidence,
        "source": source,
        "evidence_count": 1,
        "created_at": now,
        "last_activated": now,
        "last_decayed": now,
        "access_count": 1,
        "metadata": {},
    }


# R-F699 (2026-05-18) — sharded neurons helpers ─────────────────────────────

def _split_neurons_into_shards(neurons: dict) -> list[dict]:
    """Split the in-memory neurons dict into shards each carrying at
    most NEURONS_SHARD_MAX_PER_SHARD entries (and roughly fitting the
    NEURONS_SHARD_TARGET_BYTES gzipped size on average — caller writes
    JSON, gzip is downstream)."""
    if not neurons:
        return [{"neurons": {}, "shard_index": 0, "shard_count": 1}]
    # Stable order: sort by neuron-id (dict iteration is insertion-order
    # in CPython 3.7+ but we want deterministic split across reboots).
    ids = sorted(neurons.keys())
    n_total = len(ids)
    n_shards = max(1, (n_total + NEURONS_SHARD_MAX_PER_SHARD - 1)
                       // NEURONS_SHARD_MAX_PER_SHARD)
    shards: list[dict] = []
    per_shard = (n_total + n_shards - 1) // n_shards
    for i in range(n_shards):
        start = i * per_shard
        end = min(start + per_shard, n_total)
        chunk = {nid: neurons[nid] for nid in ids[start:end]}
        shards.append({
            "neurons": chunk,
            "shard_index": i,
            "shard_count": n_shards,
        })
    return shards


def _merge_neurons_shards(shards: list[dict]) -> dict:
    """Reassemble shards into a single neurons dict ordered by shard_index."""
    if not shards:
        return {}
    sorted_shards = sorted(
        shards, key=lambda s: s.get("shard_index", 0)
        if isinstance(s, dict) else 0,
    )
    merged: dict = {}
    for s in sorted_shards:
        if not isinstance(s, dict):
            continue
        chunk = s.get("neurons")
        if isinstance(chunk, dict):
            merged.update(chunk)
    return merged


async def _save_neurons_sharded(neurons: dict) -> dict:
    """Write the neurons dict as N sharded keys + a meta key.

    Returns {shard_count, total_neurons, total_bytes} for the caller's
    log line. Meta written LAST so a reader either sees the full sharded
    set or falls back to legacy NEURONS_KEY (atomic-ish across keys)."""
    # R-F714 (2026-05-19): sharding + json.dumps for every shard ran
    # synchronously on the event loop. With ~10k+ neurons across 18
    # shards this could stall for seconds. Encode in a worker thread.
    def _encode_shards():
        s = _split_neurons_into_shards(neurons)
        encoded = []
        total = 0
        for sh in s:
            enc = json.dumps(sh)
            total += len(enc)
            encoded.append((sh["shard_index"], enc))
        return s, encoded, total

    # R-F787 — throttle the encoder thread against knowledge +
    # intel_ledger so concurrent flushes from one absorb don't pile
    # up GIL-holding json+gzip threads (the 5-holder / 213s stall
    # case captured in wedge_674).
    from ._snapshot_throttle import run_in_thread_throttled
    shards, encoded_shards, total_bytes = await run_in_thread_throttled(_encode_shards)
    write_tasks = []
    for idx, encoded in encoded_shards:
        key = NEURONS_SHARD_KEY_FMT.format(i=idx)
        write_tasks.append(rs.set(key, encoded))
    if write_tasks:
        await asyncio.gather(*write_tasks, return_exceptions=True)
    n_shards = len(shards)
    meta = {
        "version": 2,
        "shard_count": n_shards,
        "total_neurons": len(neurons),
        "total_bytes": total_bytes,
        "written_at": time.time(),
    }
    await rs.set(NEURONS_SHARD_META_KEY, json.dumps(meta))
    return meta


async def _load_neurons_sharded() -> dict | None:
    """Read the sharded neurons set. Returns None if the meta key is
    absent (signals: fall back to legacy NEURONS_KEY)."""
    # R-F4173 — STRICT. The non-strict reader collapses a store-layer failure
    # and an absent key into the same None (the R-F1 contract), so a timeout
    # here read as "there is no sharded form" and fell through to an empty load.
    meta_raw = await rs.get_strict(NEURONS_SHARD_META_KEY)
    if not meta_raw:
        return None
    try:
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    except Exception:
        return None
    if not isinstance(meta, dict) or "shard_count" not in meta:
        return None
    n_shards = int(meta.get("shard_count", 0))
    if n_shards <= 0:
        return None
    keys = [NEURONS_SHARD_KEY_FMT.format(i=i) for i in range(n_shards)]
    raws = await asyncio.gather(
        *(rs.get_strict(k) for k in keys), return_exceptions=True,
    )
    # R-F4173 — a store FAILURE is not "no sharded form". Falling back to the
    # legacy key after the store has already refused us is how an empty graph
    # gets adopted as fact; propagate instead.
    for _r in raws:
        if isinstance(_r, StoreReadError):
            raise _r
    if any(isinstance(r, Exception) or r is None for r in raws):
        return None  # partial set — caller falls back to legacy
    # R-F2176: offload the json decode + merge off the event loop (same loop-stall
    # class as the edges path; the R-F371 disk-reload guard calls this at runtime).
    return await asyncio.to_thread(_decode_merge_neuron_shards, raws)


def _decode_merge_neuron_shards(raws: list) -> dict | None:
    """R-F2176 — pure CPU neuron-shard json-decode+merge, run in a worker thread.
    Returns None if any shard fails to parse (→ caller falls back to legacy)."""
    shards: list[dict] = []
    for r in raws:
        try:
            s = json.loads(r) if isinstance(r, str) else r
            if isinstance(s, dict):
                shards.append(s)
        except Exception:
            return None
    return _merge_neurons_shards(shards)


# ── R-F2082 — sharded edges helpers ─────────────────────────────────────────
def _edge_shard_index(group_key: str) -> int:
    """Stable shard for an edges group key (a neuron id). crc32 is fast and
    deterministic across reboots, so a group always maps to the same shard
    (no resharding) for as long as EDGES_SHARD_COUNT is fixed."""
    return zlib.crc32(group_key.encode("utf-8", "ignore")) % EDGES_SHARD_COUNT


def _mark_edge_shards_dirty(*group_keys: str) -> None:
    """Record that the given edge groups changed → their shards need rewriting
    on the next persist. Sync (called from the event loop's mutators), so no
    lock needed."""
    for k in group_keys:
        _dirty_edge_shards.add(_edge_shard_index(k))


def _mark_all_edge_shards_dirty() -> None:
    """A graph-wide mutation (decay) touches every group → every shard dirty."""
    _dirty_edge_shards.update(range(EDGES_SHARD_COUNT))


async def _save_edges_sharded() -> dict:
    """R-F2082 — write only the edge shards that changed since the last persist
    (or ALL shards on the first persist after a legacy-blob boot, to migrate).

    Each shard is encoded in its OWN throttled worker call with the loop free
    between shards, so per-persist CPU is O(changed shards) and never holds the
    GIL long enough to wedge — regardless of total graph size."""
    global _edges_sharded_initialized
    force_all = not _edges_sharded_initialized
    shards_to_write = set(range(EDGES_SHARD_COUNT)) if force_all else set(_dirty_edge_shards)
    if not shards_to_write:
        return {"written": 0, "shard_count": EDGES_SHARD_COUNT}

    # Clear the dirty marks NOW (before any await) so a concurrent mutation during
    # the encode/write re-dirties its shard and we don't lose it. On failure we
    # restore the marks so the next persist retries.
    _dirty_edge_shards.difference_update(shards_to_write)

    # Build per-shard snapshots SYNCHRONOUSLY (no await) so _edges isn't mutated
    # mid-iteration, and COPY each inner dict so the encoder thread reads a stable
    # snapshot even if the live graph is mutated while it runs.
    per_shard: dict[int, dict] = {i: {} for i in shards_to_write}
    for gkey, targets in _edges.items():
        bucket = per_shard.get(_edge_shard_index(gkey))
        if bucket is not None:
            bucket[gkey] = dict(targets)

    try:
        from ._snapshot_throttle import run_in_thread_throttled
        for si, d in per_shard.items():
            # Encode one shard off-loop (small → bounded GIL hold), then write it,
            # yielding to the loop between shards. Awaiting each write immediately
            # (rather than gathering deferred coroutines) keeps writes ordered on
            # the single state-store connection and lets the loop breathe.
            blob = await run_in_thread_throttled(_encode_edges, d)
            await rs.set(EDGES_SHARD_KEY_FMT.format(i=si), blob)
        # Meta LAST so a reader sees a complete sharded set or falls back to legacy.
        await rs.set(EDGES_SHARD_META_KEY, json.dumps({
            "version": 1,
            "shard_count": EDGES_SHARD_COUNT,
            "written_at": time.time(),
        }))
        _edges_sharded_initialized = True
        if force_all:
            # §21a — wire the one-time migration success to the brain (low-noise:
            # fires once per process when the legacy blob is sharded).
            try:
                wire_success(module="neural_memory",
                             summary=f"R-F2082 edges migrated to {EDGES_SHARD_COUNT} shards",
                             source_id="neural_memory:R-F2082")
            except Exception:
                pass
        return {"written": len(per_shard), "shard_count": EDGES_SHARD_COUNT, "full": force_all}
    except Exception:
        _dirty_edge_shards.update(shards_to_write)  # restore → retry next persist
        raise


async def _load_edges_sharded() -> dict | None:
    """Read the sharded edges set, merging all shards. Returns None when the meta
    key is absent or the shard set is incomplete (→ caller falls back to the
    legacy single EDGES_KEY blob)."""
    # R-F4173 — STRICT; see _load_neurons_sharded.
    meta_raw = await rs.get_strict(EDGES_SHARD_META_KEY)
    if not meta_raw:
        return None
    try:
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    except Exception:
        return None
    if not isinstance(meta, dict) or "shard_count" not in meta:
        return None
    n_shards = int(meta.get("shard_count", 0))
    if n_shards <= 0:
        return None
    keys = [EDGES_SHARD_KEY_FMT.format(i=i) for i in range(n_shards)]
    raws = await asyncio.gather(*(rs.get_strict(k) for k in keys),
                                return_exceptions=True)
    # R-F4173 — see _load_neurons_sharded: propagate a store failure rather
    # than silently degrading to the legacy path.
    for _r in raws:
        if isinstance(_r, StoreReadError):
            raise _r
    if any(isinstance(r, Exception) for r in raws):
        return None  # partial set — fall back to legacy
    # R-F2176: offload the CPU-bound gzip-decompress + json decode + merge to a
    # worker thread. Run inline on the event loop it held the GIL for multi-second
    # bursts — wedge_681 captured the main thread in _decode_edges → gzip.decompress
    # during a _persist R-F371 disk-reload, stalling the loop 5.4s. gzip releases
    # the GIL while decompressing, so a worker thread lets the loop keep running.
    return await asyncio.to_thread(_decode_merge_edge_shards, raws)


def _decode_merge_edge_shards(raws: list) -> dict:
    """R-F2176 — pure CPU edge-shard decode+merge, run in a worker thread (off the
    event loop). An empty/None shard is fine (no groups hashed there)."""
    merged: dict = {}
    for r in raws:
        if r is None:
            continue
        d = _decode_edges(r)
        if isinstance(d, dict):
            merged.update(d)
    return merged


async def _peek_neurons_disk_count() -> int:
    """R-F371 disk-check support: return the on-disk neuron count from
    the sharded meta key. Falls back to len(legacy NEURONS_KEY) on miss.
    Cheap — reads one small meta key in the happy path."""
    try:
        meta_raw = await rs.get(NEURONS_SHARD_META_KEY)
        if meta_raw:
            meta = (json.loads(meta_raw)
                    if isinstance(meta_raw, str) else meta_raw)
            if isinstance(meta, dict) and "total_neurons" in meta:
                return int(meta["total_neurons"])
    except Exception:
        pass
    try:
        legacy = await rs.get_json(NEURONS_KEY)
        if isinstance(legacy, dict):
            return len(legacy)
    except Exception:
        pass
    return 0


# ── Init ─────────────────────────────────────────────────────────────────────
@fail_wire(module="neural_memory", gap_type="embedder_failure")
async def init() -> None:
    global _neurons, _edges, _meta, _loaded, _edges_dirty, _edges_sharded_initialized
    global _load_complete
    _load_complete = False
    try:
        # R-F699: prefer sharded read; fall back to legacy NEURONS_KEY
        # if sharded meta absent or shard set is incomplete.
        neurons_raw = await _load_neurons_sharded()
        if neurons_raw is None:
            neurons_raw = await rs.get_json_strict(NEURONS_KEY)   # R-F4173
        # R-F2082: prefer the sharded edges read; fall back to the legacy single
        # EDGES_KEY blob (gzipped or raw) which the first persist then migrates
        # to shards. _decode_edges handles the GZ1: prefix on the legacy blob.
        sharded_edges = await _load_edges_sharded()
        if sharded_edges is not None:
            edges_raw = sharded_edges
            _loaded_from_shards = True
        else:
            edges_value = await rs.get_strict(EDGES_KEY)          # R-F4173
            edges_raw = _decode_edges(edges_value)
            _loaded_from_shards = False
        meta_raw = await rs.get_json_strict(NEURAL_META_KEY)      # R-F4173

        if neurons_raw and isinstance(neurons_raw, dict):
            _neurons = neurons_raw
            # R-F2200: index the bulk-loaded neurons INCREMENTALLY (yields to the
            # loop every 5k) so the boot warmup never stalls the event loop.
            await _rebuild_neuron_index_incremental()  # R-F1932/R-F2200
        if edges_raw and isinstance(edges_raw, dict):
            _edges = defaultdict(dict, {k: v for k, v in edges_raw.items()})
        if meta_raw and isinstance(meta_raw, dict):
            _meta.update(meta_raw)

        if not _meta.get("born"):
            _meta["born"] = time.time()

        # R-F2082: decide whether the first persist must (re)write edges.
        #  - loaded from shards  → already in the new form, in sync, nothing to do.
        #  - legacy blob present → migrate to shards on the first persist (dirty +
        #    sharded_initialized=False forces a full shard write once).
        #  - nothing on disk     → fresh; first mutation will dirty + write.
        if _loaded_from_shards:
            _edges_dirty = False
            _edges_sharded_initialized = True
        elif isinstance(edges_raw, dict) and len(edges_raw) > 0:
            _edges_dirty = True            # migrate legacy single-blob → shards
            _edges_sharded_initialized = False
        else:
            _edges_dirty = False
            _edges_sharded_initialized = False

        _loaded = True
        # R-F4173 — every store read for the graph succeeded. This is the ONLY
        # place it is set True; a genuinely absent key still reaches here, so a
        # fresh volume boots clean.
        _load_complete = True
        logger.info("Neural memory loaded: %d neurons, %d edge groups (R-F442 edges_dirty=%s)",
                     len(_neurons), len(_edges), _edges_dirty)
        # R-F1512: boot-time cold-edges sweep for existing oversized neurons
        _offload_sweep_all()
    except StoreReadError as e:
        # R-F4173 — THE STORE REFUSED US. Distinct from a genuinely empty
        # store: we know nothing about the graph, so we must not let anything
        # downstream read the empty in-memory copy as a measurement. `_loaded`
        # keeps its "init has run" meaning; `_load_complete` stays False.
        logger.warning(
            "[R-F4173] neural memory init could not READ the store (%s) — the "
            "graph is UNLOADED, not empty. Boot-state counters from this "
            "process are not comparable.", e,
        )
        _loaded = True
        _load_complete = False
        try:
            wire_failure(
                module="neural_memory",
                detail=f"store read failed during init: {e}",
                gap_type="engine_failure",
                source="neural_memory:init",
            )
        except Exception:
            pass
    except Exception as e:
        logger.warning("Neural memory init failed: %s", e)
        _loaded = True
        _load_complete = False   # R-F4173


async def _persist() -> None:
    global _edges_dirty, _edges_sharded_initialized
    try:
        _meta["total_neurons"] = len(_neurons)
        _meta["total_edges"] = sum(len(v) for v in _edges.values())

        # R-F371 (2026-05-12) — regression guard against the migration-race
        # overwrite pattern. Per the infinite-memory rule
        # (aria_infinite_memory.md), neural counters NEVER shrink across a
        # healthy deploy. Live evidence 2026-05-12 13:07:20 R-F251 boot
        # diagnostic: neural_neurons 11391 → 1413 (-87.6%). Race trace:
        #   1. init() at boot reads NEURONS_KEY from SQLite (pre-migration
        #      partial state) → loads 1413 neurons into _neurons.
        #   2. Operator triggers Upstash→SQLite migration mid-session.
        #      Migration writes the 11391-neuron blob from Upstash to
        #      SQLite, overwriting the SQLite NEURONS_KEY.
        #   3. Periodic _persist() fires, writes in-memory 1413 to
        #      SQLite, OVERWRITES the freshly-migrated 11391 → 9978
        #      neurons silently lost.
        # Same pattern as R-F267/F268 mastery/regional/stats scaffold-write
        # protection. Mechanism: read the on-disk value; if it has
        # significantly MORE neurons than memory, the disk is the source
        # of truth (most likely a migration just landed). Reload from disk
        # into memory and SKIP the write.
        # R-F699: cheap count-only check from sharded meta (1 small read)
        # instead of pulling the entire 4MB legacy NEURONS_KEY. Only if
        # the on-disk count is suspiciously larger do we pull full state.
        try:
            disk_count = await _peek_neurons_disk_count()
            if disk_count > max(len(_neurons), 0) * 1.1:
                # Disk has materially more — reload full state from disk
                # (paying the bigger read this once, infinite-memory
                # protection).
                disk_neurons = await _load_neurons_sharded()
                if disk_neurons is None:
                    disk_neurons = await rs.get_json(NEURONS_KEY)
                if isinstance(disk_neurons, dict):
                    _neurons.clear()
                    _neurons.update(disk_neurons)
                    await _rebuild_neuron_index_incremental()  # R-F1932/R-F2200: loop-safe re-index
                    try:
                        # R-F2082: prefer sharded edges on the disk-reload too;
                        # the legacy EDGES_KEY goes stale after migration.
                        disk_edges = await _load_edges_sharded()
                        if disk_edges is None:
                            # R-F2176: offload the legacy-blob gzip decode too.
                            _legacy_edges_raw = await rs.get(EDGES_KEY)
                            disk_edges = await asyncio.to_thread(_decode_edges, _legacy_edges_raw)
                        if isinstance(disk_edges, dict):
                            _edges.clear()
                            _edges.update(disk_edges)
                            _dirty_edge_shards.clear()
                            _edges_sharded_initialized = True
                    except Exception:
                        pass
                    _meta["total_neurons"] = len(_neurons)
                    _meta["total_edges"] = sum(len(v) for v in _edges.values())
                    _edges_dirty = False  # R-F442: just reloaded from disk, in sync
                    logger.warning(
                        "[neural] R-F371 — disk had %d neurons but in-memory only "
                        "had %d before reload. Reloaded from disk instead of "
                        "overwriting (infinite-memory protection).",
                        len(_neurons), _meta.get("total_neurons", 0),
                    )
                    return  # don't overwrite — disk already has the larger snapshot
        except Exception as _guard_err:
            logger.debug("[neural] R-F371 disk-check failed (non-fatal): %s", _guard_err)

        # No TTL — neural memory is permanent. Activation decay still
        # applies (that's learning, not forgetting), but neurons never
        # expire from Redis. Was 90d TTL before 2026-04-21.
        #
        # R-F442 (2026-05-13): only touch edges when _edges_dirty. recall()
        # persists frequently but never mutates _edges, so most _persist() calls
        # skip the edges write entirely.
        write_edges = _edges_dirty

        # R-F699 (2026-05-18) — sharded neurons write replaces the
        # legacy single 4MB blob SET.
        save_result = await _save_neurons_sharded(dict(_neurons))
        if write_edges:
            # R-F2082 — SHARDED edges write: re-encodes ONLY the shards whose
            # edges changed since the last persist (or all shards once, to migrate
            # off the legacy blob), each in its own throttled worker call with the
            # loop free between. This replaces the single ~12 MB blob whose
            # whole-graph json+gzip re-encode held the GIL 7-8s and wedged the
            # loop (R-F2081 cut the constant; this eliminates the class).
            edges_result = await _save_edges_sharded()
            if edges_result.get("full"):
                logger.info("[neural] R-F2082 migrated edges to %d shards",
                            edges_result.get("shard_count"))
        await rs.set_json(NEURAL_META_KEY, _meta)
        if save_result.get("shard_count", 1) > 1:
            # Only log when actually sharded (avoid noise for tiny states)
            logger.debug(
                "[neural] R-F699 sharded write: %d neurons in %d shards "
                "(%d total bytes)",
                save_result.get("total_neurons", 0),
                save_result.get("shard_count", 0),
                save_result.get("total_bytes", 0),
            )

        # R-F442: clear the dirty flag AFTER a successful write. If the
        # write raised, we keep the flag True so the next persist retries.
        if write_edges:
            _edges_dirty = False
    except Exception as e:
        logger.warning("Neural memory persist failed: %s", e)


async def _maybe_persist() -> None:
    """R-F986 — debounced persist for HIGH-FREQUENCY callers (learn_from_text =
    the absorb storm; recall = the chat retrieval path). _persist() re-gzips the
    full neuron set (sharded) — a GIL-bound CPU burst; firing it on every absorb
    and every recall piled onto the event loop and slowed chats. The persisted
    state is a FULL snapshot of live _neurons, so it is safe to skip when the last
    persist was < interval ago: the next call after the interval flushes the
    current (complete) state. Infrequent callers (learn_explicit / consolidate /
    migration / shutdown) keep calling _persist() directly for immediate
    durability. No data is dropped — the in-memory graph stays complete; worst
    case on an ungraceful crash is up to interval seconds of re-learnable
    learning delayed, so the infinite-memory rule holds.
    """
    global _last_neurons_write
    now = time.time()
    if (now - _last_neurons_write) < _NEURONS_MIN_WRITE_INTERVAL_S:
        return
    _last_neurons_write = now
    await _persist()


# ── Neuron Operations ────────────────────────────────────────────────────────

def _find_neuron(concept: str) -> Optional[dict]:
    """Find neuron by concept (case-insensitive). R-F1932: O(1) via the concept
    index instead of an O(all-neurons) scan on every _find_or_create."""
    nid = _concept_to_id.get(concept.strip().lower())
    return _neurons.get(nid) if nid else None


def _find_or_create(concept: str, category: str = "general",
                    source: str = "auto", confidence: str = "ASSESSED") -> dict:
    """Get existing neuron or create a new one."""
    existing = _find_neuron(concept)
    if existing:
        # Boost activation on access
        existing["activation"] = min(1.0, existing["activation"] + ACTIVATION_BOOST)
        existing["last_activated"] = time.time()
        existing["access_count"] = existing.get("access_count", 0) + 1
        _meta["total_activations"] = _meta.get("total_activations", 0) + 1
        return existing

    # Create new neuron
    neuron = _make_neuron(concept, category, source, confidence)
    _neurons[neuron["id"]] = neuron
    _index_neuron(neuron)  # R-F1932: keep the inverted indices in sync
    _meta["total_activations"] = _meta.get("total_activations", 0) + 1

    # R-F240 (2026-05-11) — warn-only at WARN_NEURONS, no pruning.
    # Pre-R-F240 _prune_weakest deleted the lowest-activation neurons
    # when count > 50K. That violates the infinite-memory rule. Now
    # neurons accumulate forever; the warn-threshold gives operator
    # visibility for cold-storage offload planning.
    if len(_neurons) > WARN_NEURONS:
        global _neural_warn_throttle
        _neural_warn_throttle += 1
        if _neural_warn_throttle % 500 == 1:
            logger.warning(
                "[neural] R-F240 — neuron count %d > warn threshold %d. "
                "NOT pruning (infinite-memory rule). Plan cold-storage offload.",
                len(_neurons), WARN_NEURONS,
            )

    return neuron


def _offload_cold_edges(from_id: str) -> int:
    """R-F1512: move weakest edges of an oversized neuron to cold storage.

    When a neuron exceeds _MAX_HOT_EDGES_PER_NEURON, the weakest edges
    (lowest weight) are moved to a separate Redis key so they don't
    degrade neural tier performance. Cold edges are still queryable —
    they're reloaded when the neuron is specifically activated.

    Returns the number of edges offloaded.
    """
    global _edges_dirty
    edges = _edges.get(from_id, {})
    if len(edges) <= _MAX_HOT_EDGES_PER_NEURON:
        return 0

    # Sort by weight ascending, keep the strongest _MAX_HOT_EDGES_PER_NEURON
    sorted_edges = sorted(edges.items(), key=lambda x: x[1])
    keep = dict(sorted_edges[-_MAX_HOT_EDGES_PER_NEURON:])
    cold = dict(sorted_edges[:-_MAX_HOT_EDGES_PER_NEURON])

    _edges[from_id] = keep
    _edges_dirty = True
    _mark_edge_shards_dirty(from_id)  # R-F2082

    # Persist cold edges to Redis (fire-and-forget, never blocks)
    try:
        import asyncio as _aio
        from . import redis_store as _rs
        cold_key = f"{_COLD_EDGES_KEY_PREFIX}{from_id}"
        _aio.create_task(_rs.set_json(cold_key, cold, ex=30 * 86400))
    except Exception:
        pass

    logger.info(
        "[neural] R-F1512 — offloaded %d cold edges from neuron %s "
        "(kept %d hot edges)",
        len(cold), from_id[:12], len(keep),
    )
    return len(cold)


def _strengthen_edge(from_id: str, to_id: str, boost: float = CO_OCCURRENCE_BOOST) -> None:
    """Strengthen connection between two neurons."""
    global _edges_dirty
    if from_id == to_id:
        return
    current = _edges[from_id].get(to_id, 0.0)
    _edges[from_id][to_id] = min(1.0, current + boost)
    # Bidirectional
    current_rev = _edges[to_id].get(from_id, 0.0)
    _edges[to_id][from_id] = min(1.0, current_rev + boost)
    # R-F442: this is the hot mutator that recall() does NOT touch.
    _edges_dirty = True
    _mark_edge_shards_dirty(from_id, to_id)  # R-F2082 — only these 2 shards rewrite next persist

    # R-F1512: soft cap — offload weakest edges when a neuron exceeds
    # _MAX_HOT_EDGES_PER_NEURON. This replaces the old warn-only approach
    # that let neurons grow to 7113+ edges and caused neural tier timeouts.
    # Cold edges are preserved (infinite-memory rule) but not loaded into
    # the hot edges dict on every absorb cycle.
    if len(_edges[from_id]) > _MAX_HOT_EDGES_PER_NEURON:
        _offload_cold_edges(from_id)
    if len(_edges[to_id]) > _MAX_HOT_EDGES_PER_NEURON:
        _offload_cold_edges(to_id)


# R-F240 (2026-05-11) — _prune_edges + _prune_weakest were DELETED.
# Both functions removed neurons / edges from the in-memory store —
# violating the "ARIA has infinite memory" operator rule
# (memory/aria_infinite_memory.md). All callers have been switched
# to warn-only paths in add() and _strengthen_edge() above. If
# capacity becomes a real concern, the right answer is offload-to-
# cold (move oldest-activation neurons to a separate slower store)
# rather than deletion.


_LAST_GLOBAL_DECAY = 0.0  # epoch seconds — protects against race condition on rapid recall

def _apply_decay() -> None:
    """Apply time-based decay to all neurons and edges.

    Race-condition fix: previously each neuron tracked its own ``last_decayed`` and was
    skipped if <0.5 days had passed. That meant two recall() calls within 12 hours would
    silently DOUBLE-counted the activation boost (the second skipped decay) producing
    spurious reinforcement. We now use a single global timestamp so decay either applies
    to *all* neurons consistently or to none, and we still apply per-neuron exact maths.
    """
    global _LAST_GLOBAL_DECAY, _edges_dirty
    now = time.time()
    days_since_global = (now - _LAST_GLOBAL_DECAY) / 86400 if _LAST_GLOBAL_DECAY else 1.0
    if days_since_global < 0.25:  # at most 4 decay passes per day
        return
    _LAST_GLOBAL_DECAY = now
    # R-F442: this pass mutates every edge weight, so the edges blob
    # needs writing on the next _persist().
    _edges_dirty = True
    _mark_all_edge_shards_dirty()  # R-F2082 — decay touches every group

    for n in _neurons.values():
        days_since_decay = (now - n.get("last_decayed", now)) / 86400
        if days_since_decay <= 0:
            continue
        decay = DECAY_RATE ** days_since_decay
        # Critical-knowledge protection: CONFIRMED facts decay 50% slower
        if n.get("confidence") == "CONFIRMED":
            decay = decay ** 0.5
        n["activation"] = max(MIN_ACTIVATION, n["activation"] * decay)
        n["last_decayed"] = now

    # Decay edges proportional to elapsed time, not per-call.
    # R-F240 (2026-05-11) — floor weak edges at MIN_EDGE_WEIGHT instead
    # of deleting. Pre-R-F240 edges below PRUNE_THRESHOLD (0.08) were
    # `del edges[to_id]` — that's forgetting which neurons were once
    # connected. Now edges decay TO MIN_EDGE_WEIGHT (0.001) and stay,
    # so forensic "did ARIA ever associate X with Y?" still resolves.
    # The recall ranking naturally suppresses 0.001-strength edges
    # (they fall to the bottom of any sort by weight), so this has
    # zero retrieval-quality impact.
    edge_decay = 0.998 ** days_since_global
    for from_id in list(_edges.keys()):
        edges = _edges[from_id]
        for to_id in list(edges.keys()):
            edges[to_id] = max(MIN_EDGE_WEIGHT, edges[to_id] * edge_decay)
        # Note: previously `if not edges: del _edges[from_id]` would
        # remove an empty edge-map. Under R-F240 edges never reach zero
        # so the map never empties — the dict entry persists with the
        # neuron's connection history intact.


def _apply_decay_safe() -> None:
    """Wrapper around _apply_decay that catches and wires failures.
    R-F2108: called from asyncio.to_thread so exceptions must be caught here."""
    try:
        _apply_decay()
    except Exception as _de:
        logger.error("neural_memory._apply_decay failed: %s", _de)
        try:
            wire_failure(module="neural_memory", detail=f"_apply_decay: {_de}",
                         gap_type="engine_failure", source="neural_memory._apply_decay")
        except Exception:
            pass


# ── Concept Extraction ───────────────────────────────────────────────────────

# Categories for auto-detection
_MARKET_PATTERNS = re.compile(
    r'\b(angola|mozambique|cape verde|guinea.bissau|'
    r'nigeria|kenya|south africa|ghana|senegal|ethiopia|'
    r'indonesia|philippines|vietnam|uae|saudi arabia|poland|'
    r'brazil|colombia|peru|taiwan|ukraine|turkey|egypt|'
    r'rwanda|uganda|cameroon|chad|mali|burkina faso|niger)\b',
    re.IGNORECASE
)
_OEM_PATTERNS = re.compile(
    r'\b(paramount|elbit|baykar|norinco|rheinmetall|thales|'
    r'leonardo|bae systems|lockheed|boeing|raytheon|'
    r'saab|embraer|denel|airbus|mbda|rafael|'
    r'iai|general dynamics|northrop|l3harris|hanwha)\b',
    re.IGNORECASE
)
_CAPABILITY_PATTERNS = re.compile(
    r'\b(uav|drone|mrap|apv|frigate|corvette|patrol vessel|'
    r'fighter|radar|c4isr|cyber|ew|counter.?ied|'
    r'artillery|mortar|small arms|ammunition|helicopter|'
    r'missile|torpedo|submarine|transport|logistics|'
    r'surveillance|intelligence|communications|satcom)\b',
    re.IGNORECASE
)
_REGULATION_PATTERNS = re.compile(
    r'\b(itar|ear|ofac|ofsi|eu sanctions|arms embargo|'
    r'export licence|end.user certificate|brokering|'
    r'dual.use|wassenaar|mtcr|nsg|australia group)\b',
    re.IGNORECASE
)
_EVENT_PATTERNS = re.compile(
    r'\b(tender|rfp|rfq|contract award|mou|'
    r'defence budget|procurement|acquisition|'
    r'military exercise|coup|conflict|election|'
    r'conference|exhibition|idex|dsei|aad|'
    r'arms deal|offset|fms notification)\b',
    re.IGNORECASE
)
# Person names — military leaders, defence ministers, key decision-makers
_PERSON_PATTERNS = re.compile(
    r'\b(general|admiral|marshal|colonel|brigadier|minister|secretary|president|'
    r'chief of staff|commander|cdr|maj gen|lt gen|gen\.|adm\.|col\.|brig\.)\s+'
    r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b',
    re.IGNORECASE
)
# Organisation / institution patterns
_ORGANISATION_PATTERNS = re.compile(
    r'\b(ministry of defence|ministry of defense|mod|dod|pentagon|'
    r'nato|african union|ecowas|sadc|asean|gcc|cplp|'
    r'un security council|european council|european commission)\b',
    re.IGNORECASE
)
# Weapons systems and platforms — specific designations
_WEAPONS_SYSTEM_PATTERNS = re.compile(
    r'\b(f-35|f-16|f/a-18|su-35|su-30|mig-29|j-10|j-20|rafale|eurofighter|gripen|'
    r'typhoon|tejas|kf-21|fa-50|jf-17|'
    r'leopard 2|abrams|challenger|k2|altay|t-90|type 99|arjun|'
    r'bayraktar tb2|tb3|anka|wing loong|mq-9|mq-1|heron|hermes|'
    r'patriot|s-400|s-300|iron dome|thaad|nasams|iris-t|'
    r'himars|m777|k9|caesar|pzh 2000|archer|'
    r'sigma|meko|gowind|fremm|type 31|mogami|'
    r'javelin|spike|stinger|nlaw|atacms|scalp|storm shadow|'
    r'brahmos|harpoon|exocet|nsm|lrasm)\b',
    re.IGNORECASE
)
# Financial and commercial terms
_FINANCIAL_PATTERNS = re.compile(
    r'\b(billion|million|usd|eur|gbp|contract value|deal worth|'
    r'budget allocation|defence spending|gdp|credit line|'
    r'letter of credit|loan agreement|sovereign guarantee|'
    r'offset obligation|countertrade|industrial participation|'
    r'down payment|milestone payment|life.?cycle cost|unit cost)\b',
    re.IGNORECASE
)

VALID_CATEGORIES = frozenset({
    "market", "oem", "capability", "regulation", "event",
    "person", "organisation", "general",
    # Abstract / doctrinal terms the LLM legitimately surfaces (F25 fix
    # 2026-04-27: DeepSeek emitted 'concept' for "Affordable Mass", which
    # was being remapped to 'general' with a noisy warning every time).
    # These don't trigger conflict detection (which only fires for
    # person/organisation/oem) so they're stored as plain neurons.
    "concept", "doctrine", "platform", "technology", "program",
    # F61 fix 2026-04-28: 'policy' surfaced organically for
    # "FIFA Human Rights Policy" and similar institutional positions
    # that aren't legally-binding regulation. Adding here preserves the
    # semantic tier instead of remapping to 'general'. Same rationale
    # as F25 — distinct from 'regulation' (binding) and 'doctrine'
    # (military). Doesn't trigger conflict detection.
    "policy",
})


async def _extract_concepts_llm(text: str, llm) -> list[tuple[str, str]]:
    """Use an LLM to extract named entities that regex patterns would miss.

    Returns a list of (concept, category) tuples.  Falls back to an empty
    list on any error or if the text is too short to justify an LLM call.
    """
    if not text or len(text) < 50:
        return []

    prompt = (
        "Extract named entities from the following text.  Return ONLY valid JSON "
        "with this schema — no commentary:\n"
        '{"entities": [{"concept": "...", "category": "market|oem|capability|'
        'regulation|event|person|organisation|general"}]}\n\n'
        "Rules:\n"
        "- concept: the entity exactly as it appears (preserve case)\n"
        "- category: one of the listed values\n"
        "- Only include entities that are meaningful nouns / proper names\n"
        "- Skip generic words like 'the', 'system', 'report'\n"
        "- Maximum 30 entities\n\n"
        f"TEXT:\n{text[:3000]}"
    )

    try:
        # The fallback chain has a 15s floor: if `remaining < 15.0`, every
        # provider (anthropic / deepseek / groq) is skipped. The previous
        # 10s timeout silently zeroed every neural-memory extraction call
        # — sweep ingest never grew the graph because all 20-30 per-call
        # extractions died with "Fallback budget exhausted (10.0s remaining)".
        # 30s gives anthropic real room on a 600-token JSON extract while
        # still bounding each call.
        # R-F2897 — the single highest-volume autonomous LLM site in the tree:
        # 20-30 extractions per sweep ingest, plus one per article the reading
        # loop reads (~9.6 sessions/day). Post-flip every one of those would hit
        # full-price Sonnet. Entity extraction from a 3k-char window into a fixed
        # JSON shape is mechanical high-recall work — exactly Haiku's job. A
        # non-Claude provider ignores a claude id, so this is a no-op until the
        # switch flips.
        try:
            from ..llm import tier_router as _tr2897
            _extract_model = _tr2897.claude_model_for_intent("entity_extraction")
        except Exception:
            _extract_model = ""
        # R-F2914 — attribute the spend. 74% of July's LLM cost landed in
        # "uncategorized" ($53.28 of $71.66), which made every "what does X
        # cost?" question unanswerable. This is the highest-volume LLM site in
        # the tree, and it had no cost_tracker.feature() scope at all.
        from . import cost_tracker as _ct2914
        with _ct2914.feature("neural_memory"):
            result = await asyncio.wait_for(
                llm.complete(
                    "You are a concise entity-extraction assistant. "
                    "Return only the requested JSON, nothing else.",
                    prompt,
                    max_tokens=600,
                    timeout=30.0,
                    model=_extract_model,   # R-F2897 — Haiku (ignored by non-Claude)
                ),
                timeout=35.0,  # outer safety net
            )
        raw = result.text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        entities = data.get("entities") or []
        pairs: list[tuple[str, str]] = []
        for ent in entities:
            concept = (ent.get("concept") or "").strip()
            category = (ent.get("category") or "general").strip().lower()
            if not concept or len(concept) < 2:
                continue
            if category not in VALID_CATEGORIES:
                # Surface the remap so a drifting LLM vocabulary (e.g. a new
                # category like "technology_tier" emerging from defence
                # white-papers) doesn't silently lose its signal. Before
                # this log, every new category was collapsed into "general"
                # with no audit trail — the neuron was created but its
                # intended tier was lost.
                logger.info(
                    "[neural_memory] LLM returned unknown category %r for concept %r; remapping to 'general'",
                    category, concept[:60],
                )
                category = "general"
            pairs.append((concept, category))
        return pairs
    except Exception as e:
        logger.debug("LLM concept extraction failed (graceful fallback): %s", e)
        return []


@fail_wire(module="neural_memory", gap_type="embedder_failure")
def extract_concepts(text: str) -> list[tuple[str, str]]:
    """Extract (concept, category) pairs from text."""
    if not text:
        return []

    concepts = []
    seen = set()
    t = text[:5000]  # Cap for performance

    for pattern, category in [
        (_MARKET_PATTERNS, "market"),
        (_OEM_PATTERNS, "oem"),
        (_CAPABILITY_PATTERNS, "capability"),
        (_REGULATION_PATTERNS, "regulation"),
        (_EVENT_PATTERNS, "event"),
        (_PERSON_PATTERNS, "person"),
        (_ORGANISATION_PATTERNS, "organisation"),
        (_WEAPONS_SYSTEM_PATTERNS, "capability"),
        (_FINANCIAL_PATTERNS, "event"),
    ]:
        for m in pattern.finditer(t):
            concept = m.group(0).strip()
            key = concept.lower()
            if key not in seen and len(key) > 2:
                seen.add(key)
                concepts.append((concept, category))

    return concepts


# ── Public API ───────────────────────────────────────────────────────────────

# R-F1512: boot-time cold-edges sweep — offload existing oversized neurons
_offload_sweep_done = False


def _offload_sweep_all() -> int:
    global _offload_sweep_done
    if _offload_sweep_done:
        return 0
    _offload_sweep_done = True
    total = 0
    for neuron_id in list(_edges.keys()):
        if len(_edges[neuron_id]) > _MAX_HOT_EDGES_PER_NEURON:
            total += _offload_cold_edges(neuron_id)
    if total:
        logger.info(
            "[neural] R-F1512 boot sweep: offloaded %d cold edges across %d neurons",
            total,
            sum(1 for n in _edges.values() if len(n) > _MAX_HOT_EDGES_PER_NEURON),
        )
    return total


@fail_wire(module="neural_memory", gap_type="embedder_failure")
async def learn_from_text(text: str, source: str = "conversation",
                          confidence: str = "ASSESSED",
                          llm=None) -> dict:
    """Extract concepts from text and grow the neural network.

    If *llm* is provided, also runs LLM-based extraction to catch novel
    entities that the regex patterns miss, then merges the two result sets.
    """
    concepts = extract_concepts(text)

    # Supplement with LLM extraction when an LLM provider is available
    if llm is not None:
        try:
            llm_concepts = await _extract_concepts_llm(text, llm)
            if llm_concepts:
                seen = {c.lower() for c, _ in concepts}
                for concept, category in llm_concepts:
                    key = concept.strip().lower()
                    if key not in seen:
                        seen.add(key)
                        concepts.append((concept, category))
        except Exception as e:
            logger.debug("LLM concept merge failed: %s", e)

    if not concepts:
        return {"neurons_activated": 0, "connections_formed": 0}

    # Conflict detection — check if new text contradicts existing knowledge.
    # R-F771: pass *source* through so detect_conflict can skip autonomous-
    # loop re-absorbs that flooded the queue with false-positive entries.
    conflicts_found = []
    for concept, category in concepts:
        if category in ("person", "organisation", "oem"):
            conflict = detect_conflict(concept, text, source=source)
            if conflict:
                conflicts_found.append(conflict)
                await log_conflict(conflict)

    neurons = []
    for concept, category in concepts:
        n = _find_or_create(concept, category, source, confidence)
        neurons.append(n)

    # Form connections between co-occurring concepts
    connections = 0
    for i, n1 in enumerate(neurons):
        for n2 in neurons[i + 1:]:
            _strengthen_edge(n1["id"], n2["id"])
            connections += 1

    # Also connect to recently activated neurons (cross-signal learning)
    # This creates associations between concepts from different signals
    import time as _time
    recent_cutoff = _time.time() - 300  # last 5 minutes
    recent_neurons = [
        n for n in _neurons.values()
        if n.get("last_activated", 0) > recent_cutoff
        and n["id"] not in {nn["id"] for nn in neurons}
    ]
    for n1 in neurons:
        for n2 in recent_neurons[:5]:  # max 5 cross-connections
            _strengthen_edge(n1["id"], n2["id"], boost=CO_OCCURRENCE_BOOST * 0.5)
            connections += 1

    await _maybe_persist()  # R-F986 — debounced (storm driver)
    return {
        "neurons_activated": len(neurons),
        "connections_formed": connections,
        "concepts": [c for c, _ in concepts],
        "conflicts": conflicts_found,
    }


@fail_wire(module="neural_memory", gap_type="embedder_failure")
async def learn_explicit(concept: str, category: str, related_to: list[str] = None,
                         source: str = "user", confidence: str = "CONFIRMED",
                         metadata: dict = None) -> dict:
    """Explicitly teach ARIA a concept and its relationships."""
    neuron = _find_or_create(concept, category, source, confidence)
    neuron["evidence_count"] = neuron.get("evidence_count", 0) + 1
    if confidence == "CONFIRMED":
        neuron["confidence"] = "CONFIRMED"
    if metadata:
        neuron["metadata"].update(metadata)

    connections = 0
    if related_to:
        for rel in related_to:
            rel_neuron = _find_or_create(rel, "general", source)
            _strengthen_edge(neuron["id"], rel_neuron["id"], boost=0.2)
            connections += 1

    await _persist()
    return {"neuron_id": neuron["id"], "concept": concept, "connections": connections}


@fail_wire(module="neural_memory", gap_type="embedder_failure")
async def recall(
    query: str,
    depth: int = 2,
    max_results: int = 20,
    category_filter: list[str] | None = None,
    recency_boost: bool = True,
) -> dict:
    """Associative recall via spreading activation through the neuron graph.

    Args:
        query: Free-text query — concepts will be extracted from it.
        depth: BFS depth for spreading activation (max 3).
        max_results: Cap on returned neurons.
        category_filter: If set, restrict spreading to neurons in these categories
            (e.g. ["market", "oem"] to ignore irrelevant noise).
        recency_boost: If True, freshly-activated neurons get a relevance multiplier
            so 10-minute-old signals don't get drowned out by 30-day-old neurons.
    """
    # R-F2108 (ARIA brain DD): offload _apply_decay to a thread so a multi-second
    # walk of ~2.4M edges never wedges the event loop on the chat-recall path.
    # _apply_decay is a pure-Python loop over _neurons.values() — no shared state
    # mutation during iteration (it mutates neuron dicts in-place, which is safe
    # because the GIL serialises dict item access). Offload to keep the loop free.
    await asyncio.to_thread(_apply_decay_safe)
    depth = max(0, min(depth, 3))

    # Find seed neurons matching query
    concepts = extract_concepts(query)
    query_words = set(query.lower().split())

    cat_set = set(category_filter) if category_filter else None
    now = time.time()

    def _passes_filter(neuron: dict) -> bool:
        if cat_set is None:
            return True
        return neuron.get("category", "general") in cat_set

    def _recency_factor(neuron: dict) -> float:
        """1.5x for last hour, 1.2x for last day, 1.0x for last week, 0.8x older."""
        if not recency_boost:
            return 1.0
        age_h = (now - neuron.get("last_activated", now)) / 3600
        if age_h < 1: return 1.5
        if age_h < 24: return 1.2
        if age_h < 24 * 7: return 1.0
        if age_h < 24 * 30: return 0.85
        return 0.7

    # R-F1932 (G4 tail): gather ONLY candidate neurons that share a word with the
    # query or an extracted concept (via the inverted index), instead of scanning
    # every neuron. The scoring below is unchanged — it just runs over candidates.
    _cand_words = set(query_words)
    for c, _ in concepts:
        _cand_words.update(c.lower().split())
    _cand_ids: set = set()
    for w in _cand_words:
        ids = _word_to_ids.get(w)
        if ids:
            _cand_ids |= ids
    # R-F2102 — a very common query word (a long contract-review message has many)
    # can map to tens of thousands of neurons; cap the candidate set so the
    # seed-scoring loop below stays bounded instead of O(huge).
    if len(_cand_ids) > _RECALL_MAX_CANDIDATES:
        import itertools as _it_cand
        _cand_ids = set(_it_cand.islice(_cand_ids, _RECALL_MAX_CANDIDATES))

    seeds = []
    for nid in _cand_ids:
        n = _neurons.get(nid)
        if n is None or not _passes_filter(n):
            continue
        score = 0.0
        for c, _ in concepts:
            if c.lower() == n["concept"]:
                score += 1.0
        concept_words = set(n["concept"].split())
        overlap = len(query_words & concept_words)
        if overlap:
            score += overlap * 0.3
        if score > 0:
            seeds.append((n, score * _recency_factor(n)))

    seeds.sort(key=lambda x: -x[1])
    seeds = seeds[:10]

    if not seeds:
        return {"query": query, "neurons": [], "associations": [],
                "network_size": len(_neurons), "filtered_by": category_filter}

    # Spreading activation — BFS through connections
    activated = {}  # neuron_id → activation score
    for seed, score in seeds:
        activated[seed["id"]] = score * seed["activation"]

    for d in range(depth):
        if len(activated) >= _RECALL_MAX_NODES:
            break  # R-F2102 — stop the cross-level explosion
        new_activations = {}
        decay_factor = 0.6 ** (d + 1)  # Activation drops with distance
        # snapshot the frontier (list() is GIL-atomic, immune to a concurrent absorb)
        for nid, act in list(activated.items()):
            if len(activated) + len(new_activations) >= _RECALL_MAX_NODES:
                break  # R-F2102 — bound total activated nodes
            _en = _edges.get(nid)
            if not _en:
                continue
            # R-F2102 — a node can hold up to 5000 edges; snapshot (GIL-atomic, so a
            # concurrent edge mutation can't raise dict-changed-size) and cap how many
            # we spread through so one high-degree hub can't blow up the traversal.
            _items = list(_en.items())
            if len(_items) > _RECALL_MAX_EDGES_PER_NODE:
                _items = _items[:_RECALL_MAX_EDGES_PER_NODE]
            for target_id, edge_weight in _items:
                if target_id in activated:
                    continue
                target_neuron = _neurons.get(target_id)
                if not target_neuron or not _passes_filter(target_neuron):
                    continue
                spread = act * edge_weight * decay_factor * target_neuron["activation"] * _recency_factor(target_neuron)
                if spread > 0.01:
                    if target_id not in new_activations or new_activations[target_id] < spread:
                        new_activations[target_id] = spread
        activated.update(new_activations)

    # Sort by activation and return
    results = []
    for nid, act_score in sorted(activated.items(), key=lambda x: -x[1])[:max_results]:
        neuron = _neurons.get(nid)
        if not neuron:
            continue
        connections = []
        for target_id, weight in sorted(_edges.get(nid, {}).items(), key=lambda x: -x[1])[:5]:
            target = _neurons.get(target_id)
            if target:
                connections.append({"concept": target["label"], "weight": round(weight, 3)})
        results.append({
            "concept": neuron["label"],
            "category": neuron["category"],
            "activation": round(neuron["activation"], 3),
            "confidence": neuron["confidence"],
            "evidence": neuron["evidence_count"],
            "relevance": round(act_score, 3),
            "connections": connections,
        })

    # Boost accessed neurons
    for seed, _ in seeds:
        seed["activation"] = min(1.0, seed["activation"] + ACTIVATION_BOOST * 0.5)
        seed["last_activated"] = time.time()

    await _maybe_persist()  # R-F986 — debounced (chat retrieval path)

    return {
        "query": query,
        "neurons": results,
        "associations": [r["concept"] for r in results if r["relevance"] > 0.05],
        "network_size": len(_neurons),
        "total_activations": _meta.get("total_activations", 0),
    }


@fail_wire(module="neural_memory", gap_type="embedder_failure")
async def get_neural_context(message: str) -> str:
    """Build context string from neural recall for injection into ARIA prompts."""
    result = await recall(message, depth=2, max_results=15)
    if not result["neurons"]:
        return ""

    lines = ["\n\n[NEURAL MEMORY — ARIA's learned associations]"]
    for n in result["neurons"][:10]:
        conn_str = ", ".join(c["concept"] for c in n["connections"][:3])
        line = f"  [{n['confidence']}] {n['concept']} ({n['category']}) — activation {n['activation']}"
        if conn_str:
            line += f" — linked to: {conn_str}"
        lines.append(line)

    return "\n".join(lines)


@fail_wire(module="neural_memory", gap_type="embedder_failure")
async def consolidate() -> dict:
    """Nightly memory consolidation — strengthen strong, abstract schemas.

    Weak-neuron pruning was removed 2026-04-21: operator asked for forever
    memory. Neurons still decay in activation (making them less retrievable
    under query) but are never deleted — they're recoverable if new evidence
    reactivates them.
    """
    now = time.time()
    seven_days_ago = now - 7 * 86400
    neurons_before = len(_neurons)
    edges_before = sum(len(v) for v in _edges.values())
    pruned_ids: list[str] = []  # kept for return-shape compatibility

    # ── 2. Strengthen frequently-accessed neurons ────────────────────────
    strengthened = 0
    for n in _neurons.values():
        if (n.get("access_count", 0) >= 5
                and n.get("last_activated", 0) > seven_days_ago):
            n["activation"] = min(1.0, n["activation"] * 1.05)
            strengthened += 1

    # ── 3. Schema extraction — abstraction over similar facts ────────────
    # When 3+ neurons in the same category share strong edges, create a parent
    # "schema" neuron that abstracts the pattern. This is how ARIA learns
    # generalisations like "Lusophone procurement pattern" instead of memorising
    # 30 individual Angola/Mozambique deal facts in isolation.
    schemas_created = 0
    try:
        from collections import defaultdict as _dd
        cat_buckets: dict[str, list[dict]] = _dd(list)
        for n in _neurons.values():
            cat_buckets[n.get("category", "general")].append(n)

        for category, neurons_in_cat in cat_buckets.items():
            if category in ("general", "schema") or len(neurons_in_cat) < 6:
                continue
            # Find the most-strongly-interconnected cluster in this category
            edge_counts: dict[str, int] = {}
            for n in neurons_in_cat:
                outgoing = _edges.get(n["id"], {})
                same_cat_links = sum(
                    1 for tid in outgoing
                    if _neurons.get(tid, {}).get("category") == category
                )
                edge_counts[n["id"]] = same_cat_links

            hubs = sorted(edge_counts.items(), key=lambda x: -x[1])[:5]
            if not hubs or hubs[0][1] < 3:
                continue

            hub_concepts = [_neurons[hid]["label"] for hid, _ in hubs if hid in _neurons]
            if len(hub_concepts) < 3:
                continue

            schema_label = f"schema:{category}:{hub_concepts[0]}"
            if _find_neuron(schema_label):
                continue  # already abstracted

            schema_neuron = _find_or_create(
                schema_label, category="schema",
                source="consolidation", confidence="ASSESSED",
            )
            schema_neuron["metadata"] = {
                "abstracts": hub_concepts,
                "parent_category": category,
                "created_by": "consolidation",
            }
            # Link schema to its constituent hubs
            for hid, _ in hubs:
                if hid in _neurons:
                    _strengthen_edge(schema_neuron["id"], hid, boost=0.4)
            schemas_created += 1
    except Exception as e:
        logger.warning("Schema extraction failed: %s", e)

    # ── 4. Daily decay pass ──────────────────────────────────────────────
    _apply_decay()

    # ── 5. Persist ───────────────────────────────────────────────────────
    await _persist()

    neurons_after = len(_neurons)
    edges_after = sum(len(v) for v in _edges.values())

    report = {
        "neurons_before": neurons_before,
        "neurons_after": neurons_after,
        "neurons_pruned": len(pruned_ids),
        "neurons_strengthened": strengthened,
        "schemas_created": schemas_created,
        "edges_before": edges_before,
        "edges_after": edges_after,
        "timestamp": now,
    }
    logger.info("Neural consolidation: pruned %d, strengthened %d, schemas %d, %d → %d neurons",
                len(pruned_ids), strengthened, schemas_created, neurons_before, neurons_after)
    return report


@fail_wire(module="neural_memory", gap_type="embedder_failure")
async def get_stats() -> dict:
    """Return neural network statistics."""
    _apply_decay()
    if not _neurons:
        return {
            "total_neurons": 0,
            "total_edges": 0,
            "total_activations": 0,
            "categories": {},
            "strongest_neurons": [],
            "born": _meta.get("born"),
            "age_days": 0,
            # R-F2951 — the graph loads via an async incremental boot warmup; a 0
            # here before `_loaded` means "not loaded yet", NOT "0 edges". The
            # boot state-regression detector (R-F251) must not read this 0 as a
            # -100% neural_edges regression (which resets the Phase A gate-#3 streak).
            "loaded": _loaded,
            # R-F4173 (C-185) — the honest one: True only when every store read
            # succeeded. `loaded` alone cannot distinguish an empty graph from
            # an unreadable one.
            "load_complete": _load_complete,
        }

    categories = defaultdict(int)
    for n in _neurons.values():
        categories[n["category"]] += 1

    strongest = sorted(_neurons.values(), key=lambda n: -n["activation"])[:10]
    # R-F4174 — `or`, not a .get() default. `_meta` is declared at module scope
    # with the KEY PRESENT holding None, so the default can never fire; the call
    # reads as defaulted and is not. R-F4173 made this reachable: strict store
    # reads abort init() BEFORE the line that repairs `born`, where the old
    # non-strict read let init finish and set it. Live result was HTTP 500 on
    # /api/aria/neural/stats — the surface that exists to REPORT a failed init
    # must not require init to have succeeded.
    born = _meta.get("born") or time.time()

    return {
        "total_neurons": len(_neurons),
        "total_edges": sum(len(v) for v in _edges.values()),
        "total_activations": _meta.get("total_activations", 0),
        "categories": dict(categories),
        "strongest_neurons": [
            {
                "concept": n["label"],
                "category": n["category"],
                "activation": round(n["activation"], 3),
                "confidence": n["confidence"],
                "evidence": n["evidence_count"],
                "connections": len(_edges.get(n["id"], {})),
            }
            for n in strongest
        ],
        "born": born,
        "age_days": round((time.time() - born) / 86400, 1),
        "loaded": _loaded,  # R-F2951 — graph fully loaded (see the not-loaded branch above)
        "load_complete": _load_complete,   # R-F4173 (C-185)
    }


@fail_wire(module="neural_memory", gap_type="embedder_failure")
async def get_cluster(concept: str, depth: int = 1) -> dict:
    """Get a concept and its immediate neighborhood."""
    neuron = _find_neuron(concept)
    if not neuron:
        return {"found": False, "concept": concept}

    nodes = [{"id": neuron["id"], "concept": neuron["label"],
              "category": neuron["category"], "activation": neuron["activation"]}]
    edges_out = []

    visited = {neuron["id"]}
    frontier = [(neuron["id"], 0)]

    while frontier:
        nid, d = frontier.pop(0)
        if d >= depth:
            continue
        for target_id, weight in _edges.get(nid, {}).items():
            target = _neurons.get(target_id)
            if not target:
                continue
            edges_out.append({"from": nid, "to": target_id, "weight": round(weight, 3)})
            if target_id not in visited:
                visited.add(target_id)
                nodes.append({"id": target_id, "concept": target["label"],
                              "category": target["category"], "activation": target["activation"]})
                frontier.append((target_id, d + 1))

    return {"found": True, "concept": concept, "nodes": nodes, "edges": edges_out}


# ── Conflict Detection ──────────────────────────────────────────────────────
# When new intelligence arrives about an entity, check if it contradicts
# what ARIA already knows. Flag contradictions for human review rather
# than silently overwriting.

CONFLICT_KEY = "crucix:aria:neural_conflicts"

_HIGH_RISK_SIGNALS = {"sanctioned", "high risk", "embargo", "blacklist", "fraud",
                      "shell company", "ghost", "suspicious", "debarred", "prohibited"}
_LOW_RISK_SIGNALS = {"compliant", "low risk", "cleared", "verified", "reputable",
                     "legitimate", "approved", "clean"}

# R-F647 (2026-05-17): operator-self entities are the user's own org, not
# counterparties. They appear in nearly every eval prompt and many chat
# turns alongside risk-language because the prompts describe scenarios
# Arkmurus is screening — the resulting "conflict" entries are noise, not
# real contradictions about a counterparty. Compare entity-lowercased
# against this set; skip detect_conflict entirely on a hit.
_OPERATOR_SELF_ENTITIES = {"arkmurus", "crucix"}

# R-F771 (2026-05-21): scaffolding markers that prove the text being
# absorbed is ARIA's own response-contract / comprehension instructions,
# not actual intel about a counterparty. Live evidence: a forensic dump
# of the conflict queue (50-item slice, 5 days) was dominated by
# `chat:autonomous:WEEKLY-HW-PLATFORMS` re-runs that absorbed the same
# `[COMPREHENSION PASS — Clause 21...]` prefix dozens of times — Baykar
# logged 7x, DoD 15x, all HIGH→LOW. The keyword scan was matching
# scaffolding language ("clear", "verified", etc.) against neurons
# seeded from news (HIGH-signal). Substring match → false positive.
# Skip detect_conflict when the text starts/contains these markers.
_SCAFFOLDING_MARKERS = (
    "[COMPREHENSION PASS",
    "RESPONSE CONTRACT for this turn",
    "[INSTRUCTION CONTRACT",
)

# R-F771 — same forensic dump showed `chat:autonomous:*` sources (the
# WEEKLY-HW-PLATFORMS / DAILY-PROC-ANGOLA / MONTHLY-CRISISWATCH
# automation loops) produced ~60% of conflict entries. These loops
# re-absorb the same canonical news pages and ARIA-prompt scaffolding
# on every cycle, so they cannot introduce genuinely new counterparty
# intel — only re-run noise. Skip detect_conflict on any source prefix
# in this set.
_NOISY_SOURCE_PREFIXES = (
    "chat:autonomous:",
)


@fail_wire(module="neural_memory", gap_type="embedder_failure")
def detect_conflict(entity: str, new_text: str, source: str = "") -> dict | None:
    """Check if new intelligence about an entity contradicts stored knowledge.

    Scans new text for risk signals and compares against existing neuron
    metadata and connected neurons. Returns a conflict dict if opposing
    signals are found, or None if no conflict.

    R-F647: skips operator-self entities (Arkmurus, Crucix).
    R-F771: skips when *source* is an autonomous-loop re-run or when
    *new_text* contains ARIA's own scaffolding markers (these produced
    ~90% of false-positive conflicts in the 2026-05-21 forensic dump).
    """
    if not _loaded:
        return None  # Neural memory not initialized yet
    if entity.strip().lower() in _OPERATOR_SELF_ENTITIES:
        return None  # operator-self: not a counterparty
    # R-F771 source filter — autonomous loops cannot introduce new intel
    if source and any(source.startswith(p) for p in _NOISY_SOURCE_PREFIXES):
        return None
    # R-F771 scaffolding filter — ARIA's own prompt prefix isn't intel
    if new_text and any(m in new_text for m in _SCAFFOLDING_MARKERS):
        return None
    neuron = _find_neuron(entity)
    if not neuron:
        return None

    new_lower = new_text.lower()
    new_high = any(s in new_lower for s in _HIGH_RISK_SIGNALS)
    new_low = any(s in new_lower for s in _LOW_RISK_SIGNALS)

    if not new_high and not new_low:
        return None  # no risk signals in new text

    # Check existing neuron metadata for opposing signals
    existing_meta = json.dumps(neuron.get("metadata", {})).lower()
    existing_source = (neuron.get("source") or "").lower()
    existing_label = neuron.get("label", "").lower()

    # Also check connected neurons for context
    connected_text = ""
    for target_id, weight in _edges.get(neuron["id"], {}).items():
        if weight > 0.15:  # only strong connections
            target = _neurons.get(target_id)
            if target:
                connected_text += " " + target.get("label", "") + " " + json.dumps(target.get("metadata", {}))
    connected_lower = connected_text.lower()
    all_existing = f"{existing_meta} {existing_source} {existing_label} {connected_lower}"

    existing_high = any(s in all_existing for s in _HIGH_RISK_SIGNALS)
    existing_low = any(s in all_existing for s in _LOW_RISK_SIGNALS)

    # F71 fix 2026-04-28: real entities like "UN Security Council" have
    # neuron metadata that legitimately contains BOTH "sanctioned" and
    # "verified" signals — so existing_high AND existing_low both go
    # True. The previous gate `(new_high and existing_low)` then fired
    # but the ternary `existing_high ? HIGH_RISK : LOW_RISK` collapsed
    # the mixed state to HIGH_RISK, producing log entries like
    # `[conflict] UN Security Council: existing=HIGH_RISK new=HIGH_RISK`
    # — same value flagged as a conflict. Compute deterministic
    # assessments first, then only fire when they actually differ. Mixed
    # signals on either side mean we can't classify cleanly, so skip.
    if existing_high and existing_low:
        return None  # ambiguous existing — no clean assessment to compare
    if new_high and new_low:
        return None  # ambiguous new — same reason
    existing_assessment = "HIGH_RISK" if existing_high else ("LOW_RISK" if existing_low else None)
    new_assessment = "HIGH_RISK" if new_high else ("LOW_RISK" if new_low else None)
    if existing_assessment is None or new_assessment is None:
        return None
    if existing_assessment == new_assessment:
        return None  # same direction — not a conflict, just a re-assertion

    conflict = {
        "entity": entity,
        "neuron_id": neuron["id"],
        "existing_assessment": existing_assessment,
        "new_assessment": new_assessment,
        "existing_source": neuron.get("source", "unknown"),
        "existing_confidence": neuron.get("confidence", "ASSESSED"),
        "detected_at": time.time(),
        "new_text_preview": new_text[:300],
    }
    return conflict


@fail_wire(module="neural_memory", gap_type="embedder_failure")
async def log_conflict(conflict: dict) -> None:
    """Store a detected conflict for human review."""
    try:
        conflicts = await rs.get_json(CONFLICT_KEY) or []
        conflicts.append(conflict)
        # Permanent retention — was last-100 cap + 90d TTL before 2026-04-21.
        await rs.set_json(CONFLICT_KEY, conflicts)
        logger.warning(
            "[conflict] %s: existing=%s new=%s — flagged for review",
            conflict.get("entity"),
            conflict.get("existing_assessment"),
            conflict.get("new_assessment"),
        )
    except Exception as e:
        logger.debug("Conflict logging failed: %s", e)


@fail_wire(module="neural_memory", gap_type="embedder_failure")
async def get_conflicts(limit: int = 20) -> list[dict]:
    """Retrieve recent conflicts for review."""
    conflicts = await rs.get_json(CONFLICT_KEY) or []
    return conflicts[-limit:]


@fail_wire(module="neural_memory", gap_type="embedder_failure")
async def resolve_conflict(entity: str) -> bool:
    """Remove resolved conflicts for an entity."""
    try:
        conflicts = await rs.get_json(CONFLICT_KEY) or []
        before = len(conflicts)
        conflicts = [c for c in conflicts if c.get("entity", "").lower() != entity.lower()]
        if len(conflicts) < before:
            await rs.set_json(CONFLICT_KEY, conflicts)
            return True
        return False
    except Exception:
        return False


@fail_wire(module="neural_memory", gap_type="embedder_failure")
async def clear_all_conflicts() -> int:
    """R-F771 (2026-05-21) — drop the entire conflict list and return the
    count removed. Surfaced because the 2026-05-21 forensic dump showed
    the queue had become unusable for review: the same entities (Baykar,
    DoD, FAA) were logged dozens of times by autonomous loops absorbing
    canonical news scaffolding. Operator needs a one-shot way to reset
    so the post-R-F771-filter signal is observable from a clean baseline.
    """
    try:
        conflicts = await rs.get_json(CONFLICT_KEY) or []
        count = len(conflicts)
        if count:
            await rs.set_json(CONFLICT_KEY, [])
        return count
    except Exception:

        return 0
