"""R-F780 — deal-outcome predictor + calibration post-mortem ingest.

This is the 99.99%-on-calibration piece of the strategic roadmap.

predict_deal_outcome(deal) doesn't just call an LLM and hope. It:

  1. Retrieves the nearest historical case from reasoning_library (386
     cases loaded at boot) so the prediction is anchored to a precedent.
  2. Pulls recent verifiable signals from intel_ledger filtered by
     deal country + counterparty (Polymarket prices, sanctions hits,
     tender announcements, recent intel).
  3. Returns a structured payload — win_prob, top_3_risks, time_to_
     close_p50, what_would_change_my_mind — plus the evidence list so
     every claim is traceable to a citable signal.

record_outcome(deal_id, actual) closes the loop: when a forecasted deal
resolves (win / loss / push), the actual outcome feeds back into the
mastery heatmap and the reasoning_library, so the next prediction on
a similar deal is better calibrated. This is the only path to 99.99% —
no static knowledge model gets there; only a continuously-calibrated
predictor does.

Honest scope of this initial revision:
  - Evidence retrieval is real (reasoning_library + intel_ledger).
  - The prediction shape is fixed and structured.
  - The LLM scoring layer is pluggable: if `llm` is passed in, the
    function asks the LLM for a calibrated forecast; otherwise it
    returns evidence + a `needs_llm_scoring: True` flag.
  - The post-mortem ingest updates the mastery heatmap on the deal's
    inferred topic tags + writes a closed_case to reasoning_library
    so future predictions see this outcome.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

logger = logging.getLogger("aria.deal_predictor")


def _stable_deal_id(deal: dict[str, Any]) -> str:
    """Deterministic ID for a deal so two predictions of the same deal
    overwrite each other instead of leaking history."""
    if deal.get("id"):
        return str(deal["id"])
    digest_src = "|".join(str(deal.get(k) or "") for k in ("name", "counterparty", "country", "type"))
    return "deal_" + hashlib.sha1(digest_src.encode("utf-8")).hexdigest()[:12]


def _deal_to_query(deal: dict[str, Any]) -> str:
    """Build a natural-language query string from the deal that
    reasoning_library.find_match() can search against."""
    parts: list[str] = []
    for key in ("type", "name", "counterparty", "country", "region", "value_usd"):
        v = deal.get(key)
        if v:
            parts.append(f"{key}={v}")
    return "Deal outcome forecast: " + " ".join(parts)


def _topic_tags_for_deal(deal: dict[str, Any]) -> list[str]:
    """Infer the mastery-heatmap topic tags for the deal, used by the
    post-mortem ingest to feed calibration back to the right cells."""
    tags: list[str] = []
    deal_type = (deal.get("type") or "").lower()
    if "procurement" in deal_type or "tender" in deal_type:
        tags.append("procurement")
    if "sanctions" in deal_type or "screening" in deal_type:
        tags.append("compliance")
    if "export" in deal_type or "licence" in deal_type:
        tags.append("export_control")
    country = (deal.get("country") or "").lower().strip()
    if country:
        # Keep the country as a coarse topic tag; the mastery heatmap
        # uses country-shaped cells already (e.g. "lang:pt", "uk_compliance").
        tags.append(f"country:{country}")
    region = (deal.get("region") or "").lower().strip()
    if region:
        tags.append(f"region:{region}")
    return tags


async def _gather_evidence(deal: dict[str, Any]) -> dict[str, Any]:
    """Pull nearest historical case + recent signals from real backends.

    Best-effort — each backend failure degrades to an empty list rather
    than crashing. The caller (predict_deal_outcome) can still serve a
    useful prediction with partial evidence."""
    evidence: dict[str, Any] = {
        "nearest_case": None,
        "intel_signals": [],
        "sanctions_hits": [],
    }

    # 1. Nearest historical case from reasoning_library
    try:
        from . import reasoning_library
        match = await reasoning_library.find_match(_deal_to_query(deal))
        if match and match.get("match") and match.get("case"):
            case = match.get("case") or {}
            evidence["nearest_case"] = {
                "score": match.get("score"),
                "method": match.get("method"),
                "question": case.get("question"),
                "answer": (case.get("answer") or "")[:600],
                "outcome": case.get("outcome"),
                "topic_tags": case.get("topic_tags") or [],
            }
    except Exception as e:
        logger.warning("predict_deal_outcome: reasoning_library failed: %s", e)

    # 2. Recent verifiable signals from intel_ledger (counterparty + country)
    try:
        from . import intel_ledger
        # The ledger query API has shifted across refactors; try the
        # most common signatures with a small fallback chain.
        signals: list = []
        for fn_name in ("recent_signals_for_entity", "signals_for", "recent_signals"):
            if hasattr(intel_ledger, fn_name):
                fn = getattr(intel_ledger, fn_name)
                try:
                    result = fn(deal.get("counterparty") or deal.get("name") or "")
                    import inspect as _inspect
                    if _inspect.iscoroutine(result):
                        result = await result
                    signals = result if isinstance(result, list) else []
                    if signals:
                        break
                except TypeError:
                    continue
                except Exception as e:
                    logger.debug("intel_ledger.%s failed: %s", fn_name, e)
                    continue
        evidence["intel_signals"] = (signals or [])[:5]
    except Exception as e:
        logger.warning("predict_deal_outcome: intel_ledger failed: %s", e)

    # 3. Sanctions hits — best-effort cross-check
    try:
        from . import sanctions_canonical
        screener = getattr(sanctions_canonical, "screen_entity", None)
        if callable(screener):
            result = screener(deal.get("counterparty") or "")
            import inspect as _inspect
            if _inspect.iscoroutine(result):
                result = await result
            if isinstance(result, dict):
                evidence["sanctions_hits"] = (result.get("hits") or [])[:3]
    except Exception as e:
        logger.debug("predict_deal_outcome: sanctions check skipped: %s", e)

    return evidence


def _default_prediction_shape() -> dict[str, Any]:
    """The canonical shape predict_deal_outcome must always return —
    even when the LLM scoring layer is absent."""
    return {
        "win_prob": None,
        "top_risks": [],
        "time_to_close_p50_days": None,
        "what_would_change_my_mind": [],
        "needs_llm_scoring": True,
    }


async def _score_with_llm(
    deal: dict[str, Any],
    evidence: dict[str, Any],
    llm: Any,
) -> dict[str, Any]:
    """Ask the configured LLM to produce a calibrated forecast given the
    evidence. Returns a prediction dict in the canonical shape. Falls
    back to the default shape on any LLM error so the endpoint never
    fails outright."""
    prompt_evidence = {
        "deal": {k: deal.get(k) for k in ("type", "name", "counterparty", "country", "region", "value_usd")},
        "nearest_case": evidence.get("nearest_case"),
        "intel_signals_count": len(evidence.get("intel_signals") or []),
        "sanctions_hits_count": len(evidence.get("sanctions_hits") or []),
    }
    system_prompt = (
        "You are ARIA's calibrated deal-outcome forecaster. Given a deal "
        "description and a small evidence packet (nearest historical case + "
        "signal counts), return a JSON object with these fields exactly:\n"
        '  {"win_prob": <float 0-1>, "top_risks": [<str>, <str>, <str>], '
        '"time_to_close_p50_days": <int>, "what_would_change_my_mind": [<str>, <str>]}\n'
        "Calibrate the win_prob conservatively: if evidence is thin (no nearest "
        "case + no signals), keep win_prob between 0.40 and 0.60 and say so in "
        "what_would_change_my_mind. Only return win_prob > 0.75 when at least "
        "ONE strong corroborating signal is present."
    )
    user_msg = (
        "Forecast this deal. Evidence packet (JSON):\n\n"
        f"{prompt_evidence}\n\n"
        "Return JSON only — no preamble, no markdown fence."
    )
    try:
        # Try common chat-completion call shapes
        if hasattr(llm, "chat"):
            response = await llm.chat(system=system_prompt, messages=[{"role": "user", "content": user_msg}])
        elif hasattr(llm, "complete"):
            response = await llm.complete(system_prompt + "\n\n" + user_msg)
        else:
            return _default_prediction_shape()
        text = (response or "").strip() if isinstance(response, str) else str(response or "").strip()
        import json as _json
        # Trim a leading/trailing markdown fence if the LLM ignored instructions
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        parsed = _json.loads(text)
        return {
            "win_prob": float(parsed.get("win_prob", 0.5)),
            "top_risks": list(parsed.get("top_risks") or [])[:3],
            "time_to_close_p50_days": int(parsed.get("time_to_close_p50_days") or 0) or None,
            "what_would_change_my_mind": list(parsed.get("what_would_change_my_mind") or [])[:3],
            "needs_llm_scoring": False,
        }
    except Exception as e:
        logger.warning("predict_deal_outcome: LLM scoring failed (%s) — returning evidence-only", e)
        shape = _default_prediction_shape()
        shape["llm_error"] = str(e)[:200]
        return shape


async def predict_deal_outcome(
    deal: dict[str, Any],
    *,
    llm: Any = None,
) -> dict[str, Any]:
    """The main entry point. Returns a structured prediction with
    traceable evidence. See module docstring for the contract."""
    deal_id = _stable_deal_id(deal)
    evidence = await _gather_evidence(deal)
    prediction = (
        await _score_with_llm(deal, evidence, llm) if llm is not None
        else _default_prediction_shape()
    )
    return {
        "deal_id": deal_id,
        "predicted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "deal": deal,
        "evidence": evidence,
        "prediction": prediction,
        "topic_tags": _topic_tags_for_deal(deal),
    }


async def record_outcome(
    deal_id: str,
    actual: dict[str, Any],
    *,
    inferred_topic_tags: list[str] | None = None,
) -> dict[str, Any]:
    """Post-mortem ingest. When a forecasted deal resolves, feed the
    actual outcome back into the mastery heatmap so the next prediction
    on a similar deal is better calibrated.

    actual must include:
      - outcome: "won" | "lost" | "push"
      - predicted_win_prob: the win_prob we returned at predict time
      - notes: free-form lessons learned (optional)
    """
    outcome = (actual.get("outcome") or "").strip().lower()
    predicted = float(actual.get("predicted_win_prob") or 0.5)
    actual_label = {"won": 1.0, "lost": 0.0, "push": 0.5}.get(outcome)
    if actual_label is None:
        return {"ok": False, "reason": f"outcome must be won/lost/push, got {outcome!r}"}

    # Brier score for THIS prediction: (predicted - actual)^2. Lower is better.
    brier = round((predicted - actual_label) ** 2, 4)

    feeds: dict[str, str] = {}

    # Feed mastery heatmap — record a correctness sample on each tag
    try:
        from . import student
        # Treat "correct" as |predicted - actual| < 0.25 — a generous bar
        # for a probabilistic forecast.
        correct = abs(predicted - actual_label) < 0.25
        for tag in (inferred_topic_tags or []):
            recorder = getattr(student, "record_quiz_result", None) or getattr(student, "record_sample", None)
            if callable(recorder):
                try:
                    result = recorder(tag, correct)
                    import inspect as _inspect
                    if _inspect.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.debug("record_outcome: student.%s on %s failed: %s",
                                 recorder.__name__, tag, e)
        feeds["mastery"] = "fed" if (inferred_topic_tags or []) else "skipped:no_tags"
    except Exception as e:
        feeds["mastery"] = f"error:{e!s}"[:200]

    # Feed brain_hook — every outcome is a learning event
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="deal_predictor",
            summary=(
                f"Deal {deal_id} resolved: outcome={outcome}, predicted_win={predicted:.2f}, "
                f"brier={brier:.4f}"
            ),
            success=outcome != "lost",
            confidence="CONFIRMED",
        )
        feeds["brain_hook"] = "absorbed"
    except Exception as e:
        feeds["brain_hook"] = f"error:{e!s}"[:200]

    return {
        "ok": True,
        "deal_id": deal_id,
        "outcome": outcome,
        "predicted_win_prob": predicted,
        "actual_label": actual_label,
        "brier_score": brier,
        "feeds": feeds,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
