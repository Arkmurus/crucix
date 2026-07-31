"""R-F3512 — capture raw source responses so FROZEN gold evidence can exist.

WHY THIS EXISTS. Twice now a piece of work has stopped at the same wall: R-F3482 and
R-F3501 could gate the derivation and the report-assembly layer, but not a full
orchestrator replay, because "frozen RAW source responses do not exist". Without them no
gold case can prove retrieval recall, resolver precision or end-to-end rendering, and the
consolidation started in R-F3510 cannot accumulate real-state evidence. This removes that
wall by recording what the source adapters actually returned on a real run.

A RECORDING OF DD EVIDENCE IS PERSONAL DATA. It contains officer names, dates of birth,
addresses and sanctions-screening results about identified people. Writing it to disk
without the machinery built earlier today would have created exactly the unmanaged,
unerasable store that R-F3478/R-F3484/R-F3488/R-F3490 exist to prevent — so:

  * OFF by default. Recording is opt-in per run (`ARIA_DD_RECORD_EVIDENCE=1`); a normal
    customer DD writes nothing.
  * Written under the data-subject envelope: subject key, retention class, jurisdiction
    and lawful basis travel WITH the recording, so `erase_by_subject` can reach it and
    `retention_review` can see it.
  * Written to a gitignored path. A gold corpus is committed deliberately, after a human
    has decided the record may be retained — never swept in by an incidental `git add`.

WHAT IT IS NOT: it is not a cache, and nothing reads it during a run. It is a capture for
building a replayable corpus, which is a deliberate, human-initiated act.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.dd_recorder")

#: Recordings land here. Gitignored: personal data is never committed by accident.
RECORDING_DIR = pathlib.Path("data/eval/dd_recordings")

#: Retention class for a captured evidence set. Deliberately the CDD class — a recording
#: of a due-diligence run is due-diligence material and inherits its period rather than
#: acquiring a longer life by being called a "fixture".
RECORDING_RETENTION_CLASS = "cdd_evidence"


from .engine_wiring import wire_failure, wire_success  # R-F3557 (§21a)


def is_recording() -> bool:
    """Opt-in, per run. A normal customer DD records nothing."""
    return str(os.getenv("ARIA_DD_RECORD_EVIDENCE", "")).strip().lower() in {
        "1", "true", "yes"}


def _safe(value: Any) -> Any:
    """JSON-safe, and never raises: a capture must not be able to fail a DD."""
    try:
        json.dumps(value)
        return value
    except Exception:
        return repr(value)[:2000]


def record_source_results(
    *,
    run_id: str,
    subject_name: str,
    subject_key: str = "",
    jurisdiction: str = "",
    labels: list[str],
    results: list[Any],
) -> dict:
    """Write one run's raw adapter responses, enveloped. Returns a small receipt.

    Exceptions from the adapters are recorded as exceptions rather than dropped: a
    replay corpus that silently omits the failures would be a corpus of happy paths,
    and the failure modes are exactly what the honesty work of this session addresses.
    """
    if not is_recording():
        return {"recorded": False, "reason": "disabled"}
    try:
        RECORDING_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": run_id,
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "subject_name": subject_name,
            # ── the data-subject envelope (R-F3488/R-F3492) ──
            "data_subject_key": subject_key or "",
            "data_jurisdiction": (jurisdiction or "").strip().lower(),
            "retention_class": RECORDING_RETENTION_CLASS,
            "lawful_basis": "legitimate_interests",
            "personal_data": True,
            "erasure_reachable": bool(subject_key),
            "sources": {
                label: (
                    {"error": type(res).__name__, "detail": str(res)[:500]}
                    if isinstance(res, BaseException) else _safe(res)
                )
                for label, res in zip(labels, results)
            },
            "note": (
                "Raw adapter responses captured for building a replayable gold corpus. "
                "Contains personal data; enveloped so erase_by_subject can reach it and "
                "retention_review can see it. Not read during any run."
            ),
        }
        path = RECORDING_DIR / f"{run_id}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        logger.info("[R-F3512] recorded %d source responses for run %s",
                    len(payload["sources"]), run_id)
        wire_success(module="dd_evidence_recorder",
                     summary=f"captured {len(payload['sources'])} source response(s) for the gold corpus",
                     source_id="dd_evidence_recorder:record_source_results")
        return {"recorded": True, "path": str(path),
                "sources": sorted(payload["sources"]),
                "erasure_reachable": payload["erasure_reachable"]}
    except Exception as e:  # noqa: BLE001 — a capture must never cost a report
        logger.debug("[R-F3512] recording skipped: %s", e)
        # R-F3557 — the swallow is correct (a capture must not cost a report) but
        # it must not be SILENT: a corpus that quietly stops recording looks
        # identical to one with nothing to record.
        wire_failure(module="dd_evidence_recorder",
                     detail=f"evidence capture failed: {type(e).__name__}",
                     gap_type="engine_failure",
                     source="dd_evidence_recorder:record_source_results")
        return {"recorded": False, "reason": f"{type(e).__name__}: {e}"}
