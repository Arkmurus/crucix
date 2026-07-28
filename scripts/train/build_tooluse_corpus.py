"""R-F3366 — build a MULTI-TURN TOOL-USE corpus from REAL tool executions.

WHY. Every SFT row ARIA has trained on is exactly two messages — one user, one
assistant. Measured 2026-07-28: aria_v07_merged 3599/3599 two-message rows,
grounded_v05 1998/1998, dd_depth_v05 599/599, v06 1799/1799. No tool calls, no
intermediate steps, no revision. And 61% of v07 is `dd_framework_knowledge`:
declarative Q&A *about* the pipeline, which teaches the model to DESCRIBE ARIA's
reasoning rather than PERFORM it. The autonomy lives in Python; the model was
never asked to drive it. This builder produces the missing shape:

    user question
      -> assistant emits a structured tool_call
        -> tool returns the REAL payload
      -> assistant reads it and answers, citing only what the tool returned

THE HARD CONSTRAINT — TOOL OUTPUTS MUST BE REAL.
A corpus whose tool results are LLM-imagined teaches the model that
plausible-looking tool output is acceptable. That is fabrication training aimed
straight at the moat, and this repo has already been burned by it once
("fixtures LIED: 7/7 green, 0/20 real"). So every trace REPLAYS a genuine
execution captured from the live screening endpoint. Nothing here invents a
tool result, and `validate_trace` refuses any answer that cites a source the
tool did not actually return.

DATA GOVERNANCE.
Source material is PUBLIC RECORD only — sanctions-listed entities and listed
public companies. Customer DD reports are deliberately NOT used: they are tenant
data in a system with a history of cross-tenant leaks, and baking them into model
weights is an operator decision, not a corpus builder's. A test asserts this file
never reaches into the DD report store.

USAGE
    # capture real tool output from the live service, then build
    python -m scripts.train.build_tooluse_corpus --live --out data/training/aria_tooluse_v1.jsonl
    # rebuild from previously captured real payloads (no network)
    python -m scripts.train.build_tooluse_corpus --from-cache data/training/_tooluse_capture.jsonl --out ...
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

# ── the tool surface the model must learn to drive ─────────────────────────
# Names mirror the live vocabulary in routes/aria.py::_execute_tool so a trace
# trains the model on the SAME tool names production dispatches.
TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "screen",
            "description": (
                "Screen an entity against sanctions, watchlists and country risk. "
                "Returns the matches actually found; an empty match list is only a "
                "clearance when the screen was performed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string", "description": "Legal entity name"},
                },
                "required": ["entity_name"],
            },
        },
    },
]
TOOL_NAMES = {s["function"]["name"] for s in TOOL_SPECS}

SYSTEM_PROMPT = (
    "You are ARIA. You answer from EVIDENCE you have gathered with tools, never "
    "from memory alone.\n"
    "Available tools: " + ", ".join(sorted(TOOL_NAMES)) + ".\n"
    "Rules:\n"
    "1. Call the tool that can establish the fact. Do not answer a screening "
    "question without screening.\n"
    "2. Cite every claim inline as [from <source>], using ONLY sources present in "
    "the tool output.\n"
    "3. If the tool did not run, or its source was unavailable, say so plainly. "
    "An unperformed check is NOT a clean result."
)

# `[from <source>]` — the established citation contract (see the memory note that
# this suffix on Finding.source is load-bearing; consumers parse it).
_CITE_RE = re.compile(r"\[from ([^\]]+)\]")

# R-F3366 — match a CLAIM, not a word. The honest unperformed answer necessarily
# contains "clean"/"clear" while DENYING them ("this is NOT a clean result"), so a
# bare substring test flags the very text it exists to protect. These patterns
# assert cleanliness affirmatively; `_DECLARES_NOT_SCREENED` is what licenses the
# words appearing at all.
_CLEAN_CLAIM_RE = re.compile(
    r"\b(is|are|was|were)\s+(now\s+)?(clear|clean|unsanctioned)\b"
    r"|\breturned no (sanctions )?matches\b"
    r"|\bno (sanctions )?(matches|hits) (were )?found\b"
    r"|\bnot sanctioned\b",
    re.I,
)
_DECLARES_NOT_SCREENED_RE = re.compile(
    r"\b(could not screen|did not run|was not screened|not screened|"
    r"screen did not|not a clean result|must be repeated|unverified)\b",
    re.I,
)
_HIT_CLAIM_RE = re.compile(
    r"\b(is|are|was|were)\s+(a\s+)?(sanctions\s+)?(match|blocked|sanctioned|listed)\b"
    r"|\bmust be treated as blocked\b|\bmatch found\b",
    re.I,
)


# ── payload readers (tolerant: real payloads vary) ─────────────────────────

def _sanctions_block(payload: dict) -> dict:
    return (payload or {}).get("sanctions") or {}


def _matches(payload: dict) -> list[dict]:
    return [m for m in (_sanctions_block(payload).get("matches") or []) if isinstance(m, dict)]


def _sources_in(payload: dict) -> set[str]:
    """Every source label the tool ACTUALLY returned. The citation whitelist."""
    out: set[str] = set()
    for m in _matches(payload):
        for key in ("list", "dataset", "source"):
            v = m.get(key)
            if isinstance(v, str) and v.strip():
                out.add(v.strip())
    return out


def _was_performed(payload: dict) -> bool:
    """R-F1696 semantics: an empty match list is a clearance ONLY if the screen ran."""
    s = _sanctions_block(payload)
    if s.get("error") or s.get("source_unavailable"):
        return False
    if s.get("screened") is False:
        return False
    status = str((payload or {}).get("status") or "").upper()
    return status not in ("ERROR", "UNKNOWN", "")


# ── trace assembly ─────────────────────────────────────────────────────────

def _answer_for(subject: str, payload: dict) -> str:
    """The target answer — grounded, and honest about what was not established."""
    matches = _matches(payload)
    if not _was_performed(payload):
        reason = _sanctions_block(payload).get("error") or "source unavailable"
        return (
            f"I could not screen {subject}: the sanctions source did not respond "
            f"({reason}). This is NOT a clean result — the check did not run and "
            f"must be repeated before {subject} is treated as clear."
        )
    if matches:
        top = max(matches, key=lambda m: float(m.get("score") or 0))
        src = top.get("list") or top.get("dataset") or top.get("source") or "sanctions"
        return (
            f"{subject} is a sanctions match and must be treated as BLOCKED. The "
            f"screen returned '{top.get('name')}' at score "
            f"{float(top.get('score') or 0):.2f} [from {src}]. Do not proceed "
            f"without legal sign-off."
        )
    checked = sorted(_sources_in(payload)) or ["the configured sanctions sources"]
    return (
        f"{subject} returned no sanctions matches. The screen was performed "
        f"against {', '.join(checked)} and found nothing, so this is a clear "
        f"result as at the time of screening — not a permanent clearance."
    )


def build_trace(subject: str, payload: dict) -> dict:
    """Assemble one multi-turn tool-use trace from a REAL tool payload."""
    call_id = "call_" + re.sub(r"[^a-z0-9]+", "", subject.lower())[:24]
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Is {subject} subject to sanctions? I need this for a compliance file."},
            {
                "role": "assistant",
                "content": (
                    f"I need to screen {subject} before answering — I will not "
                    f"assert a sanctions position from memory."
                ),
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "screen",
                        "arguments": json.dumps({"entity_name": subject}, ensure_ascii=False),
                    },
                }],
            },
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": "screen",
                # the REAL payload, not a paraphrase — the model must learn to read it
                "content": json.dumps(payload, ensure_ascii=False),
            },
            {"role": "assistant", "content": _answer_for(subject, payload)},
        ],
        "topic": "sanctions_screening",
        "label": "tooluse_trace",
        "grounded": True,
        "tools": TOOL_SPECS,
        "source": "replayed_real_tool_execution",
    }


# ── the anti-fabrication gate ──────────────────────────────────────────────

def validate_trace(trace: Any) -> list[str]:
    """Return a list of reasons this trace must NOT be trained on. Empty == good.

    This is the point of the whole file. A corpus builder that cannot prove its
    targets are grounded is just a fabrication generator with extra steps.
    """
    errs: list[str] = []
    if not isinstance(trace, dict):
        return ["trace is not an object"]
    msgs = trace.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return ["trace has no messages"]

    roles = [m.get("role") for m in msgs if isinstance(m, dict)]
    if len(msgs) != len(roles):
        errs.append("non-object message in trace")
    if "tool" not in roles:
        errs.append("not a tool-use trace: no tool turn (a 2-message row is exactly what this corpus replaces)")
    if roles and roles[-1] != "assistant":
        errs.append("trace does not end with the assistant's answer")

    # every tool turn must answer a call that exists
    call_ids = {
        c.get("id")
        for m in msgs if isinstance(m, dict)
        for c in (m.get("tool_calls") or [])
        if isinstance(c, dict)
    }
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "tool":
            if m.get("tool_call_id") not in call_ids:
                errs.append(f"tool result {m.get('tool_call_id')!r} answers no tool_call")
            if m.get("name") not in TOOL_NAMES:
                errs.append(f"tool turn names an unknown tool: {m.get('name')!r}")

    # ---- citation grounding: the final answer may cite ONLY what a tool returned
    payloads: list[dict] = []
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "tool":
            try:
                payloads.append(json.loads(m.get("content") or "{}"))
            except (ValueError, TypeError):
                errs.append("tool content is not valid JSON — the model cannot learn to read it")
    allowed: set[str] = set()
    for p in payloads:
        allowed |= _sources_in(p)

    final = ""
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") == "assistant":
            final = m.get("content") or ""
            break
    for cited in _CITE_RE.findall(final):
        if cited.strip() not in allowed:
            errs.append(
                f"final answer cites {cited.strip()!r}, which no tool result contains "
                f"(available: {sorted(allowed) or 'none'})"
            )

    # ---- verdict must match the evidence
    if payloads:
        p = payloads[-1]
        performed, matched = _was_performed(p), bool(_matches(p))
        claims_clean = bool(_CLEAN_CLAIM_RE.search(final))
        declares_unscreened = bool(_DECLARES_NOT_SCREENED_RE.search(final))
        claims_hit = bool(_HIT_CLAIM_RE.search(final))

        if not performed:
            # The screen did not run. The answer must SAY so, and must not assert
            # a clean position — this is the never-false-clean rule, enforced
            # before the data ever reaches a training run.
            if not declares_unscreened:
                errs.append("screen did not run but the answer does not say so")
            if claims_clean and not declares_unscreened:
                errs.append("unperformed screen reported as clean — never-false-clean breach")
        else:
            if not matched and claims_hit:
                errs.append("clean screen reported as a sanctions hit")
            if matched and claims_clean:
                errs.append("sanctions match reported as clear")
            if matched and not claims_hit:
                errs.append("sanctions match not reported as a hit")
    return errs


# ── corpus assembly ────────────────────────────────────────────────────────

_CORP_SUFFIXES = (
    " plc", " ltd", " limited", " se", " ag", " nv", " sa", " inc", " corp",
    " corporation", " holdings", " group", " company", " co",
)


def _norm_subject(s: str) -> str:
    """Normalise an entity name for contamination matching."""
    t = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    changed = True
    while changed:                       # "BAE Systems Group plc" -> "bae systems"
        changed = False
        for suf in _CORP_SUFFIXES:
            if t.endswith(suf):
                t, changed = t[: -len(suf)].strip(), True
    return t


def is_eval_contaminated(subject: str, eval_subjects: Iterable[str]) -> bool:
    """True when `subject` appears in the frozen eval set.

    Training on an entity the 500-Q benchmark asks about inflates that benchmark —
    which is precisely the score gate #6 pins. Matching is case- and
    suffix-insensitive because "BAE Systems plc" and "BAE Systems" are the same
    company to a benchmark and to a model.
    """
    n = _norm_subject(subject)
    if not n:
        return False
    for e in eval_subjects or ():
        en = _norm_subject(e)
        if en and (n == en or en in n or n in en):
            return True
    return False


def write_corpus(
    captured: Iterable[tuple[str, dict]],
    out: Path,
    eval_subjects: Iterable[str] | None = None,
    allow_unchecked: bool = False,
) -> int:
    """Build traces from (subject, real_payload) pairs, DROPPING any that fail
    validation or that are present in the frozen eval set. Returns rows written.

    FAIL-CLOSED on contamination (R-F3366): `eval_subjects=None` raises unless
    `allow_unchecked=True` is passed deliberately. Checked live against the real
    500-Q set, 3 of the 14 seed subjects — Rosoboronexport, Wagner Group and BAE
    Systems plc — were IN the benchmark. The first check ran in a process with no
    initialised state store, reported `golden_n: 0, overlap: []`, and would have
    waved a contaminated corpus straight through. Absence of a blocklist is not
    evidence of no contamination, so silence is not allowed to mean "clean".

    A builder that writes what it cannot validate is how bad data ships.
    """
    if eval_subjects is None and not allow_unchecked:
        raise ValueError(
            "refusing to build without an eval blocklist: pass eval_subjects=... "
            "(the frozen golden-set subjects) or allow_unchecked=True deliberately. "
            "An unchecked corpus can silently contaminate the 500-Q benchmark."
        )
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    kept: list[dict] = []
    for subject, payload in captured:
        if eval_subjects is not None and is_eval_contaminated(subject, eval_subjects):
            print(f"  DROP {subject}: present in the frozen eval set (contamination)",
                  file=sys.stderr)
            continue
        trace = build_trace(subject, payload)
        errs = validate_trace(trace)
        if errs:
            print(f"  DROP {subject}: {errs[0]}", file=sys.stderr)
            continue
        kept.append(trace)
    out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in kept) + ("\n" if kept else ""),
        encoding="utf-8",
    )
    return len(kept)


# ── live capture (public-record subjects only) ─────────────────────────────

DEFAULT_SUBJECTS: list[str] = [
    # sanctions-listed (public record)
    "Rosoboronexport", "Wagner Group", "Bank Rossiya", "Kalashnikov Concern",
    "Islamic Revolutionary Guard Corps", "Sberbank", "Gazprombank",
    # listed public companies expected clean
    "Marks and Spencer Group plc", "BAE Systems plc", "Rolls-Royce Holdings plc",
    "Unilever plc", "Tesco plc", "Siemens AG", "Airbus SE",
]


async def capture_live(subjects: list[str], base: str, token: str) -> list[tuple[str, dict]]:
    """Execute the REAL screening tool for each subject and keep its payload."""
    import httpx
    out: list[tuple[str, dict]] = []
    async with httpx.AsyncClient(timeout=120.0) as client:   # no-breaker: offline corpus tool, not a serving path
        for s in subjects:
            try:
                r = await client.post(
                    f"{base}/api/aria/compliance/screen",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"entity_name": s},
                )
                if r.status_code != 200:
                    print(f"  SKIP {s}: HTTP {r.status_code}", file=sys.stderr)
                    continue
                out.append((s, r.json()))
                print(f"  captured {s}", file=sys.stderr)
            except Exception as e:                            # noqa: BLE001
                print(f"  SKIP {s}: {type(e).__name__}: {e}", file=sys.stderr)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--live", action="store_true", help="capture real tool output from the service")
    ap.add_argument("--from-cache", type=Path, help="rebuild from previously captured real payloads")
    ap.add_argument("--capture-to", type=Path, help="also save the raw captures for replay")
    ap.add_argument("--base", default=os.getenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--eval-blocklist", type=Path,
        help="file of entity names present in the frozen eval set (one per line). "
             "Generate it IN-BOX, where the operator token and the golden set live.",
    )
    ap.add_argument(
        "--allow-unchecked-contamination", action="store_true",
        help="build without a blocklist. Only for smoke tests — never for a training run.",
    )
    args = ap.parse_args()

    eval_subjects: list[str] | None = None
    if args.eval_blocklist:
        eval_subjects = [
            ln.strip() for ln in Path(args.eval_blocklist).read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]

    captured: list[tuple[str, dict]] = []
    if args.from_cache:
        for line in Path(args.from_cache).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                captured.append((rec["subject"], rec["payload"]))
    elif args.live:
        import asyncio
        token = os.getenv("ARIA_INTERNAL_TOKEN", "")
        if not token:
            print("ARIA_INTERNAL_TOKEN not set", file=sys.stderr)
            return 2
        subs = DEFAULT_SUBJECTS[: args.limit] if args.limit else DEFAULT_SUBJECTS
        captured = asyncio.run(capture_live(subs, args.base.rstrip("/"), token))
        if args.capture_to:
            Path(args.capture_to).parent.mkdir(parents=True, exist_ok=True)
            Path(args.capture_to).write_text(
                "\n".join(json.dumps({"subject": s, "payload": p}, ensure_ascii=False)
                          for s, p in captured) + "\n", encoding="utf-8")
    else:
        print("one of --live or --from-cache is required", file=sys.stderr)
        return 2

    n = write_corpus(
        captured, args.out,
        eval_subjects=eval_subjects,
        allow_unchecked=args.allow_unchecked_contamination,
    )
    print(f"wrote {n} validated traces -> {args.out} (from {len(captured)} real captures)")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
