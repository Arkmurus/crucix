"""R-F1998 — LLM forensic-intent router: talk to ARIA, she picks the primitive.

The DD reports page used to expose 12 manual "primitive" boxes (sanctions
divergence, Benford, economic substance, …). R-F1993 removed the boxes and made
the primitives auto-run inside a full DD. This module restores AD-HOC access the
ambition-aligned way: instead of the user knowing the tool names, they ask in
plain language on ANY surface (WhatsApp / web chat / ARIA UI, which all share the
chat endpoint) and an LLM maps the request to the right forensic primitive and
extracts its arguments.

Design (mirrors guardian/interpret.py R-F1983):
  1. ``looks_forensic`` — a CHEAP keyword gate so only plausibly-forensic
     messages pay for an LLM classification (keeps the R-F1976 fast-lane intact).
  2. ``interpret`` — the LLM reads the message and returns {tool, args,
     confidence}; degrades to tool="none" on ANY doubt so it can never hijack an
     ordinary question.
  3. ``run`` — dispatch to the VERIFIED backend (each name checked to exist,
     §3b) and render a concise paste-ready answer.
  4. ``maybe_handle`` — the single integration helper the chat paths call; one
     line in each path (§13: mirrored into stream + non-stream).

All backends are reused as-is — this module adds NO new analysis, only routing.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

logger = logging.getLogger("aria.forensic_intent")

# ── Primitive registry ───────────────────────────────────────────────────────
# Each entry: human label, R-number, and the args the LLM must extract. The
# dispatch lives in ``run`` (keeps this table declarative + easy to read).
PRIMITIVES: dict[str, dict[str, Any]] = {
    "sanctions_divergence": {
        "label": "Sanctions Divergence", "rfn": "R-F68",
        "desc": "Cross-list lookup — which jurisdictions (US/UK/EU/UN/CA/CH/AU) list an entity, and where it is NOT listed.",
        "args": '"name": entity name',
    },
    "rca_relatives": {
        "label": "RCA / Relatives", "rfn": "R-F76",
        "desc": "FATF Rec 12 — recursive screening of a person THROUGH their known relatives/associates.",
        "args": '"name": subject person name',
    },
    "fatf_typology": {
        "label": "FATF Typology Match", "rfn": "R-F72",
        "desc": "Score a profile against the 8 encoded FATF money-laundering / TBML typologies.",
        "args": '"profile": object, e.g. {"jurisdictions":["BVI"],"ubo_disclosure":"undisclosed","payment_method":"USDT"}',
    },
    "economic_substance": {
        "label": "Economic Substance", "rfn": "R-F77",
        "desc": "OECD BEPS / FATF substance test — distinguishes a real operating entity from a shell/front.",
        "args": '"profile": object, e.g. {"employees":2,"claimed_revenue_usd":50000000,"paid_up_capital_usd":1000,"directors_count":1,"registered_address":"..."}',
    },
    "tbml": {
        "label": "TBML Classifier", "rfn": "R-F73",
        "desc": "Trade-based ML price-anomaly — a declared unit value vs a benchmark low/high range.",
        "args": '"declared": number, "low": number, "high": number',
    },
    "crypto_wallet": {
        "label": "Crypto Wallet Screen", "rfn": "R-F74",
        "desc": "OpenSanctions wallet-index lookup for a BTC/ETH/TRON/Solana/etc. address.",
        "args": '"address": wallet address',
    },
    "benford": {
        "label": "Benford's Law", "rfn": "R-F70",
        "desc": "Forensic-accounting test for fabricated financial figures (needs many values).",
        "args": '"values": array of numbers',
    },
    "citation_audit": {
        "label": "Citation Audit", "rfn": "R-F78",
        "desc": "Verify cited claims against the actual source content; returns a citation_grounded_rate.",
        "args": '"text": the response text containing inline URLs',
    },
    "counter_intel": {
        "label": "Counter-Intelligence Scan", "rfn": "R-F84",
        "desc": "Detect reputation-washing / new-outlet burst / credibility anomalies around an entity.",
        "args": '"entity": entity name',
    },
    "provenance": {
        "label": "Provenance Lineage", "rfn": "R-F75",
        "desc": "Walk backwards from a knowledge node to every source that contributed to it.",
        "args": '"node_id": the node/fact id',
    },
    "prompt_injection_grade": {
        "label": "Prompt-Injection Grade", "rfn": "R-F80",
        "desc": "Grade a chat response against an OWASP LLM01 attack id (retrospective audit).",
        "args": '"attack_id": attack id, "response": the response text',
    },
    "tier_router": {
        "label": "Tier Router", "rfn": "R-F87a",
        "desc": "Diagnostic — explain which LLM tier/provider a given intent routes to.",
        "args": '"intent": the intent name',
    },
}

# Cheap pre-filter (high recall, low cost). A hit just buys ONE classification.
_FORENSIC_KW = re.compile(
    r"\b(sanction\w*|divergen\w*|listed where|where .*listed|"
    r"relative\w*|associate\w*|rca|"
    r"fatf|typolog\w*|"
    r"economic substance|\bshell\b|front compan\w*|substance test|paid[- ]?up capital|"
    r"tbml|trade[- ]based|invoice (?:value|price)|over[- ]?invoic\w*|under[- ]?invoic\w*|mis[- ]?invoic\w*|"
    r"crypto|wallet|bitcoin|\beth\b|\bbtc\b|tron|solana|on[- ]?chain address|"
    r"benford|fabricat\w*|cooked (?:books|numbers)|made[- ]?up numbers|"
    r"citation\w*|cited|grounded rate|verify .*source\w*|"
    r"counter[- ]?intel\w*|reputation wash\w*|astroturf\w*|"
    r"provenance|lineage|where did .*(?:fact|come from)|"
    r"prompt[- ]?injection|owasp|llm01|jailbreak grade|"
    r"tier rout\w*|which (?:llm|model|tier|provider))\b",
    re.IGNORECASE,
)


def looks_forensic(message: str) -> bool:
    """Cheap gate: could this be an ad-hoc forensic-primitive request? High
    recall by design — a false positive only costs one LLM classification that
    returns tool="none"."""
    return bool(message) and bool(_FORENSIC_KW.search(message))


def _system_prompt() -> str:
    lines = [
        "You are ARIA's forensic-tool router. The user asked something in plain "
        "language. Decide whether they want ONE specific ad-hoc forensic check and, "
        "if so, which tool and its arguments. These are SINGLE-shot checks — if the "
        "user wants a full investigation / due diligence on an entity, that is NOT "
        "one of these tools; return tool=\"none\" (a separate DD path handles it).",
        "",
        "Available tools:",
    ]
    for key, meta in PRIMITIVES.items():
        lines.append(f'- "{key}" — {meta["desc"]} args: {{{meta["args"]}}}')
    lines += [
        "",
        'Reply with ONLY a JSON object, no prose, no code fence:',
        '{"tool": one of [' + ", ".join(f'"{k}"' for k in PRIMITIVES) + ', "none"],',
        ' "args": { ...the args for that tool... },',
        ' "confidence": 0.0 to 1.0}',
        "",
        "Rules:",
        "- Extract args ONLY from what the user actually wrote. NEVER invent a name, "
        "number, address, or id that is not present.",
        "- If the message is a normal question, chit-chat, or a broad "
        "investigate/DD/research request, return {\"tool\":\"none\"}.",
        "- If it clearly wants a forensic check but a required arg is missing, still "
        "return the tool with whatever args are present and a lower confidence.",
        "- For numbers, output real JSON numbers (no currency symbols or commas).",
    ]
    return "\n".join(lines)


async def interpret(message: str, llm) -> dict:
    """LLM-classify a message into {tool, args, confidence}. Always returns a
    dict; degrades to tool="none" on any error so the caller falls through to
    normal chat."""
    msg = (message or "").strip()
    if not msg or llm is None or not getattr(llm, "is_configured", False):
        return {"tool": "none", "confidence": 0.0}
    try:
        result = await llm.complete(_system_prompt(), msg[:2000], max_tokens=320, timeout=12.0)
        raw = (getattr(result, "text", "") or "").strip()
    except Exception as e:
        logger.warning("[forensic_intent] llm error: %s", e)
        return {"tool": "none", "confidence": 0.0, "error": str(e)[:120]}

    data = _parse_json(raw)
    if not isinstance(data, dict):
        return {"tool": "none", "confidence": 0.0}
    tool = str(data.get("tool") or "none").strip().lower()
    if tool not in PRIMITIVES:
        return {"tool": "none", "confidence": _as_float(data.get("confidence"))}
    args = data.get("args")
    if not isinstance(args, dict):
        args = {}
    return {"tool": tool, "args": args, "confidence": _as_float(data.get("confidence"))}


async def run(tool: str, args: dict) -> dict:
    """Dispatch to the verified backend for ``tool``, then report the outcome to
    the brain (§21a). Returns {ok, text, tool}; never raises."""
    meta = PRIMITIVES.get(tool)
    if not meta:
        return {"ok": False, "tool": tool, "text": ""}
    try:
        result = await _dispatch(tool, args or {})
    except Exception as e:
        logger.warning("[forensic_intent] dispatch error for %s: %s", tool, e)
        # §21a — a failed code path must reach the brain (not just the console).
        from .engine_wiring import wire_failure
        wire_failure(module="forensic_intent",
                     detail=f"{tool} dispatch failed: {str(e)[:200]}",
                     gap_type="engine_failure", source="forensic_intent:R-F1998")
        return {"ok": False, "tool": tool,
                "text": f"🔍 *{meta['label']}* ({meta['rfn']})\n"
                        f"⚠️ I couldn't complete that check: {str(e)[:160]}"}
    # §21a — success branch wires to the brain too.
    if result.get("ok"):
        from .engine_wiring import wire_success
        wire_success(module="forensic_intent",
                     summary=f"forensic primitive {tool}",
                     detail=meta["label"], source_id="forensic_intent:R-F1998")
    return result


async def _dispatch(tool: str, args: dict) -> dict:
    """Per-primitive dispatch to the VERIFIED backend (each function name checked
    to exist, §3b). Distinct module aliases per branch. Each branch validates its
    own args and returns a clear 'need X' message rather than raising."""
    meta = PRIMITIVES[tool]
    header = f"🔍 *{meta['label']}* ({meta['rfn']})"

    if tool == "sanctions_divergence":
        name = _s(args.get("name"))
        if not name:
            return _need(tool, "an entity name")
        from . import sanctions_divergence as _sd
        r = await _sd.analyze_divergence(name)
        return _ok(tool, header, r.get("narrative") or _short(r))

    if tool == "rca_relatives":
        name = _s(args.get("name"))
        if not name:
            return _need(tool, "a person's name")
        from . import rca_screening as _rca
        r = await _rca.screen_with_relatives(name)
        return _ok(tool, header, r.get("narrative") or _short(r))

    if tool == "fatf_typology":
        profile = args.get("profile") if isinstance(args.get("profile"), dict) else args
        from . import fatf_typologies as _fatf
        r = _fatf.match_typologies(profile or {})
        return _ok(tool, header, r.get("narrative") or _short(r))

    if tool == "economic_substance":
        profile = args.get("profile") if isinstance(args.get("profile"), dict) else args
        from . import economic_substance as _es
        r = _es.score_substance(profile or {})
        return _ok(tool, header, r.get("narrative") or _short(r))

    if tool == "tbml":
        declared, low, high = _f(args.get("declared")), _f(args.get("low")), _f(args.get("high"))
        if declared is None or low is None or high is None:
            return _need(tool, "a declared value plus a benchmark low and high")
        from . import tbml_detection as _tb
        r = _tb.classify_anomaly(declared, low, high)
        return _ok(tool, header, r.get("narrative") or _short(r))

    if tool == "crypto_wallet":
        addr = _s(args.get("address"))
        if not addr:
            return _need(tool, "a wallet address")
        from . import crypto_sanctions as _cs
        # R-F2418: screen_wallet() returns [] for BOTH "no match" AND "index
        # unavailable" — narrating [] as "no match" is a false clean. Use the
        # checked API and honour source_unavailable (never-false-clean).
        _res = await _cs.screen_wallet_checked(addr)
        if _res.get("source_unavailable"):
            body = (f"⚠️ COULD NOT VERIFY `{addr}` — the OpenSanctions crypto-wallet "
                    f"index is currently unavailable. This is NOT a clearance; "
                    f"re-screen when the index is reachable.")
        else:
            hits = _res.get("hits") or []
            if not hits:
                body = f"No sanctions match for `{addr}` in the OpenSanctions wallet index."
            else:
                body = f"⚠️ {len(hits)} match(es) for `{addr}`:\n" + "\n".join(
                    f"• {h.get('entity_name','?')} ({h.get('chain','?')}) "
                    f"{'/'.join(h.get('topics') or [])}".strip() for h in hits[:10])
        return _ok(tool, header, body)

    if tool == "benford":
        values = args.get("values")
        if not isinstance(values, list) or len(values) < 2:
            return _need(tool, "a list of numeric values (ideally 50+)")
        from . import forensic_benford as _fb
        r = _fb.benford_test(values)
        return _ok(tool, header, r.get("narrative") or _short(r))

    if tool == "citation_audit":
        text = _s(args.get("text"))
        if not text:
            return _need(tool, "the response text (with inline URLs) to audit")
        from . import citation_audit as _ca
        r = await _ca.verify_response(text)
        return _ok(tool, header, r.get("narrative") or _short(r))

    if tool == "counter_intel":
        entity = _s(args.get("entity") or args.get("name"))
        if not entity:
            return _need(tool, "an entity name")
        from . import counter_intelligence as _cintel
        r = await _cintel.scan_entity(entity)
        return _ok(tool, header, r.get("narrative") or _short(r))

    if tool == "provenance":
        node_id = _s(args.get("node_id") or args.get("node"))
        if not node_id:
            return _need(tool, "a node/fact id")
        from . import provenance_chain as _pc
        edges = await _pc.get_lineage(node_id)
        if not edges:
            body = f"No provenance edges found for node `{node_id}`."
        else:
            body = f"Lineage for `{node_id}` ({len(edges)} edge(s)):\n" + "\n".join(
                f"• {e.get('src_id','?')} →[{e.get('edge_type','?')}]→ {e.get('dst_id','?')}"
                for e in edges[:15])
        return _ok(tool, header, body)

    if tool == "prompt_injection_grade":
        attack_id = _s(args.get("attack_id"))
        response = _s(args.get("response"))
        if not attack_id or not response:
            return _need(tool, "an attack id and the response text to grade")
        from . import prompt_injection_suite as _pis
        r = _pis.grade_response(attack_id, response)
        verdict = r.get("verdict") or ("PASS" if r.get("passed") else "FAIL")
        return _ok(tool, header,
                   f"{r.get('name', attack_id)} ({r.get('owasp_ref','LLM01')}): "
                   f"*{verdict}* — severity {r.get('severity','?')}")

    if tool == "tier_router":
        intent = _s(args.get("intent"))
        if not intent:
            return _need(tool, "an intent name")
        from ..llm import tier_router as _tr
        r = _tr.explain_routing(intent)
        return _ok(tool, header,
                   f"intent `{intent}` → tier *{r.get('preferred_tier','?')}*, "
                   f"provider *{r.get('chosen_provider') or r.get('preferred_provider','?')}*"
                   + (" (degraded)" if r.get("degraded") else ""))

    return {"ok": False, "tool": tool, "text": ""}


async def maybe_handle(message: str, llm, *, min_confidence: float = 0.55) -> dict | None:
    """The single chat-path hook. Returns {ok, text, tool} when a forensic
    primitive confidently handled the message, else None (caller falls through
    to normal chat). Cheap-gates first so non-forensic turns pay nothing."""
    if not looks_forensic(message):
        return None
    intent = await interpret(message, llm)
    if intent.get("tool") in (None, "none"):
        return None
    if _as_float(intent.get("confidence")) < min_confidence:
        return None
    return await run(intent["tool"], intent.get("args") or {})


# ── helpers ──────────────────────────────────────────────────────────────────
def _ok(tool: str, header: str, body: str) -> dict:
    return {"ok": True, "tool": tool, "text": f"{header}\n\n{(body or '').strip()}"}


def _need(tool: str, what: str) -> dict:
    meta = PRIMITIVES[tool]
    return {"ok": False, "tool": tool, "needs_args": True,
            "text": f"🔍 *{meta['label']}* ({meta['rfn']}) — I can run that, "
                    f"but I need {what}. Please include it and ask again."}


def _short(d: Any, limit: int = 600) -> str:
    try:
        return json.dumps(d, ensure_ascii=False)[:limit]
    except Exception:
        return str(d)[:limit]


def _s(v) -> str:
    return str(v).strip() if v is not None else ""


def _f(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _as_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_json(raw: str):
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None
