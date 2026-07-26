"""R-F3109 — the single gateway for structured (JSON-shaped) model output.

WHY THIS EXISTS — measured, not assumed (2026-07-26).

There was no gateway, so every caller hand-rolled three things: how to get a
provider, how to read the reply, and what to do when the reply was unusable.
That produced a defect class, not a defect. An AST/grep sweep found THREE call
sites constructing `llm_pipeline.LLMPipeline()` — a class that has never existed
in this tree (`llm_pipeline.py` defines `LLMTrainingPipeline`). Each import sat
inside a broad `except Exception`, so all three degraded silently:

  company_investigator.py  — found and fixed by R-F2535 (went LLM-free), and its
                             own comment records "never existed"
  grounded_reasoner.py:741 — STILL BROKEN (R-F3110). Live reasoning stage, called
                             by reasoning_router.py:383. Measured: _get_llm()
                             returns None, so _extract_premises() returns [] and
                             _decompose() falls back to a stub question.
  llm_eval_framework.py:209 — STILL BROKEN (R-F3111). Its deepseek arm returned
                             "[ERROR: ...]" as the model's ANSWER.

One site was fixed and two were left, because nothing structural made the other
two impossible. That is the same shape as R-F3095 (fixing one registry row leaves
the mechanism) and R-F3101 (fixing one adapter leaves the contract). So the fix is
a gateway plus a guard (R-F3112), not three edits.

THE NORTH-STAR PROPERTY. A failed model call must never be readable as an empty
answer. On any non-ok outcome `data` is None — never `[]`, never `{}` — so a
caller cannot iterate a failure and conclude "nothing found". `raw_text` is always
preserved so a rejection can be investigated rather than guessed at. The gateway
never repairs, completes, or infers a payload: unusable output is reported as
INVALID_OUTPUT, which is the honest answer, and is the one thing a prompt cannot
be trusted to guarantee about itself.

OUTCOME VOCABULARY. Reuses `intel.sources._common` so the codebase does not grow a
third dialect (that module is the SOURCE-ADAPTER vocabulary, R-F3101; the evidence
contract's `RetrievalOutcome` is the third). `invalid_output` is defined HERE
because it is a property of a model reply, not of a source fetch; it corresponds
to `RetrievalOutcome.PARSER_FAILED` when a call is lifted into an EvidenceRecord.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from ..intel.sources._common import (
    OUTCOME_ERROR,
    OUTCOME_OK,
    OUTCOME_TIMEOUT,
    OUTCOME_UNAVAILABLE,
)
from ..intel.engine_wiring import wire_failure, wire_success

logger = logging.getLogger("aria.llm.structured")

#: The model answered, but the reply could not be parsed/validated into the
#: requested shape. Distinct from OUTCOME_UNAVAILABLE (never answered) for the
#: same reason R-F3101 separated `empty` from `unavailable`: only one of them is
#: a coverage gap, and neither is a result.
OUTCOME_INVALID_OUTPUT = "invalid_output"

#: Outcomes on which `data` is None and the caller must NOT infer an answer.
NON_ANSWERING = frozenset({
    OUTCOME_INVALID_OUTPUT, OUTCOME_UNAVAILABLE, OUTCOME_TIMEOUT, OUTCOME_ERROR,
})

_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)


@dataclass(frozen=True)
class StructuredResult:
    """The outcome of one structured model call.

    `ok` is the only field a caller should branch on for "did I get data".
    """

    outcome: str
    data: Any = None
    raw_text: str = ""
    provider: str = ""
    model: str = ""
    errors: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def ok(self) -> bool:
        return self.outcome == OUTCOME_OK

    @property
    def answered(self) -> bool:
        """True when the model produced a reply at all, usable or not.

        `not answered` is a COVERAGE GAP and must be disclosed; `answered` with
        `not ok` means the model spoke and was rejected.
        """
        return self.outcome not in (OUTCOME_UNAVAILABLE, OUTCOME_TIMEOUT, OUTCOME_ERROR)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe view — feeds run diagnostics without a second shape."""
        return {
            "outcome": self.outcome,
            "ok": self.ok,
            "provider": self.provider,
            "model": self.model,
            "errors": list(self.errors),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def _strip_fence(text: str) -> str:
    """Remove a surrounding markdown code fence, if present.

    Normalisation only — models routinely wrap JSON in ```json fences. It never
    edits the payload itself; if what is inside is not JSON, it stays not-JSON
    and the call is rejected.
    """
    match = _FENCE_RE.match(text or "")
    return match.group(1) if match else (text or "")


def _validate_shape(value: Any, schema: dict[str, Any] | None) -> list[str]:
    """Check a parsed payload against a minimal shape contract.

    Deliberately NOT jsonschema — §6 puts the burden of proof on a new
    dependency, and the shapes callers actually need are small:

        {"type": "list", "items": "str"}
        {"type": "dict", "required": ["verdict"], "types": {"score": "float"}}

    Returns a list of human-readable problems; empty means valid. Error strings
    name the field AND what was expected, per AGENTS.md §8.7.
    """
    if not schema:
        return []
    errors: list[str] = []
    kinds: dict[str, tuple] = {
        "str": (str,), "int": (int,), "float": (int, float),
        "bool": (bool,), "list": (list,), "dict": (dict,),
    }

    expected = schema.get("type")
    if expected == "list" and not isinstance(value, list):
        return [f"expected a JSON list, got {type(value).__name__}"]
    if expected == "dict" and not isinstance(value, dict):
        return [f"expected a JSON object, got {type(value).__name__}"]

    if expected == "list":
        item_kind = schema.get("items")
        if item_kind in kinds:
            for index, item in enumerate(value):
                if not isinstance(item, kinds[item_kind]):
                    errors.append(
                        f"item[{index}] expected {item_kind}, got {type(item).__name__}")
    elif expected == "dict":
        for key in schema.get("required") or ():
            if key not in value:
                errors.append(f"required key {key!r} is missing")
        for key, kind in (schema.get("types") or {}).items():
            if key in value and kind in kinds and not isinstance(value[key], kinds[kind]):
                errors.append(
                    f"key {key!r} expected {kind}, got {type(value[key]).__name__}")
    return errors


def resolve_provider(llm: Any = None) -> Any:
    """Return the injected provider or the single live application chain.

    Mirrors dd_orchestrator._resolve_dd_llm (R-F3087) deliberately: that fix
    established app.state.llm_provider as THE choke point for callers with no
    Request. Reproducing the resolution rather than inventing a third one is the
    whole point of this module.
    """
    if llm is not None:
        return llm
    try:
        from ..main import app as _app
        return getattr(getattr(_app, "state", None), "llm_provider", None)
    except Exception as exc:  # noqa: BLE001 — never raise into a caller
        logger.warning("[R-F3109] could not resolve the live LLM provider: %s", exc)
        return None


def _fail(
    outcome: str,
    *,
    caller: str,
    detail: str,
    gap_type: str,
    raw_text: str = "",
    provider: str = "",
    model: str = "",
    errors: tuple[str, ...] = (),
) -> StructuredResult:
    """Build a non-answering result and report it (§21a: failure reaches a sink)."""
    wire_failure(
        module="llm_structured",
        detail=f"{caller}: {detail}",
        gap_type=gap_type,
        source=f"llm_structured:{caller}",
    )
    return StructuredResult(
        outcome=outcome, data=None, raw_text=raw_text,
        provider=provider, model=model, errors=errors,
    )


async def call_structured(
    system_prompt: str,
    user_message: str,
    *,
    schema: dict[str, Any] | None = None,
    llm: Any = None,
    caller: str = "unknown",
    max_tokens: int = 1000,
    timeout: float = 60.0,
    model: str | None = None,
) -> StructuredResult:
    """Run one model call and return validated structured output, or INVALID_OUTPUT.

    Never raises. Every branch reaches the brain (§21a): a usable reply emits
    wire_success, and each distinct failure emits wire_failure with a gap_type the
    coder loop can route (§21e).

    `caller` is required in practice — it is what makes a rejection traceable to a
    site rather than to "some LLM call".
    """
    provider = resolve_provider(llm)
    if provider is None:
        return _fail(
            OUTCOME_UNAVAILABLE, caller=caller,
            detail="no LLM provider is configured or resolvable",
            gap_type="llm_unreachable",
        )

    provider_name = str(getattr(provider, "name", "") or "")
    try:
        result = await provider.complete(
            system_prompt, user_message,
            max_tokens=max_tokens, timeout=timeout, model=model,
        )
    except asyncio.TimeoutError:
        return _fail(
            OUTCOME_TIMEOUT, caller=caller,
            detail=f"provider {provider_name or 'chain'} timed out after {timeout}s",
            gap_type="llm_failure", provider=provider_name,
        )
    except Exception as exc:  # noqa: BLE001 — classify, never propagate
        return _fail(
            OUTCOME_ERROR, caller=caller,
            detail=f"provider {provider_name or 'chain'} failed: {exc}",
            gap_type="llm_provider_failure", provider=provider_name,
        )

    raw_text = str(getattr(result, "text", "") or "")
    served_model = str(getattr(result, "model", "") or "")
    served_via = str(getattr(result, "routed_via", "") or "")
    in_tok = int(getattr(result, "input_tokens", 0) or 0)
    out_tok = int(getattr(result, "output_tokens", 0) or 0)
    # routed_via is set by the hybrid/fallback chain; prefer it, since it names
    # the provider that ACTUALLY served rather than the one first asked.
    served_provider = served_via or provider_name

    if not raw_text.strip():
        return _fail(
            OUTCOME_INVALID_OUTPUT, caller=caller,
            detail="model returned an empty body",
            gap_type="llm_invalid_output",
            provider=served_provider, model=served_model,
            errors=("model returned an empty body",),
        )

    try:
        parsed = json.loads(_strip_fence(raw_text))
    except (json.JSONDecodeError, ValueError) as exc:
        return _fail(
            OUTCOME_INVALID_OUTPUT, caller=caller,
            detail=f"reply was not valid JSON: {exc}",
            gap_type="llm_invalid_output", raw_text=raw_text,
            provider=served_provider, model=served_model,
            errors=(f"reply was not valid JSON: {exc}",),
        )

    problems = _validate_shape(parsed, schema)
    if problems:
        return _fail(
            OUTCOME_INVALID_OUTPUT, caller=caller,
            detail="reply did not match the requested shape: " + "; ".join(problems[:3]),
            gap_type="llm_invalid_output", raw_text=raw_text,
            provider=served_provider, model=served_model,
            errors=tuple(problems),
        )

    wire_success(
        module="llm_structured",
        summary=f"{caller}: structured reply validated ({served_provider or 'chain'})",
        source_id=f"llm_structured:{caller}",
    )
    return StructuredResult(
        outcome=OUTCOME_OK, data=parsed, raw_text=raw_text,
        provider=served_provider, model=served_model,
        input_tokens=in_tok, output_tokens=out_tok,
    )
