"""Turn a raw intel alert (news snippet, URL, or free-text fragment)
into a structured BD opportunity — suitable for pushing to the
Airtable Pipeline table.

This is the glue layer between:
  - intel alerts (correlation signals, news digests, /teach submissions)
  - intel/prime_sub_map.py (prime → sub-contractor ecosystem lookup)
  - integrations/airtable_pipeline.py (Pipeline writer)

Public API
──────────
  async convert(alert: dict | str, *, push_to_airtable=True) -> dict

  `alert` can be:
    - a string (raw headline / snippet)
    - a dict with at least {"title" or "text"} and optionally
      {"url", "source", "country", "prime", "product"}

  Returns a structured brief:
    {
      "ok": bool,
      "opportunity_name": str,
      "prime": str | None,
      "product_family": str | None,
      "country": str | None,
      "sub_contractor_candidates": [...],
      "brief_markdown": str,
      "airtable": {"ok", "record_id"?, "reason"},
    }

Design notes
────────────
- Zero LLM calls. Pattern matching + lookup only. Can be invoked from
  within chat handlers WITHOUT extending the turn budget.
- ARIA's chat-response quoting this brief is the natural flow: chat
  says "here's the structured opportunity + it's been pushed to your
  Pipeline table, ID recXXXX".
- Idempotency: by default we DO push to Airtable every call (the
  Pipeline table isn't keyed on a stable id and duplicate rows are
  easier to clean up than missing rows).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger("aria.intel.opportunity_converter")


# ── Known defence-prime fingerprints (case-insensitive) ───────────────────
# Used to detect which prime an alert is about from the text alone.
# Kept narrow; anything not matched falls through to caller-provided
# `prime=` or is returned as None (no fabrication).
_PRIME_REGEX = re.compile(
    r"\b(?:"
    r"boeing|lockheed(?:\s+martin)?|lmt|"
    r"bae(?:\s+systems)?|"
    r"airbus(?:\s+defence)?|"
    r"leonardo|finmeccanica|agustawestland|"
    r"mbda|"
    r"thales|"
    r"saab|"
    r"kongsberg|kda|"
    r"raytheon|rtx|"
    r"general\s+dynamics|gdls|"
    r"rheinmetall|"
    r"knds|kmw|krauss-maffei|nexter|"
    r"dassault|"
    r"textron|bell(?:\s+textron)?"
    r")\b",
    re.IGNORECASE,
)

# Known product families — keep in sync with the keys in prime_sub_map.
# Single regex so we can catch "P-8A" / "p-8 poseidon" / "p8" consistently.
_PRODUCT_REGEX = re.compile(
    r"\b("
    r"P-?8(?:A)?(?:\s+poseidon)?|"
    r"F-?15|F-?35(?:\s+lightning)?|F-?16|"
    r"typhoon|eurofighter|"
    r"rafale|"
    r"gripen(?:\s+E?/F)?|"
    r"A(?:-|\s)?400M(?:\s+atlas)?|"
    r"C-295(?:\s+MPA)?|C-390|"
    r"C-130(?:J)?(?:\s+hercules)?|"
    r"CH-47(?:\s+chinook)?|AH-64(?:\s+apache)?|"
    r"AW101|AW149|AW159|"
    r"M-346(?:\s+master)?|"
    r"hawk(?:\s+trainer)?|"
    r"type\s*26|hunter-class\s+frigate|"
    r"leopard\s*2|puma\s+ifv|panther\s+kf51|"
    r"abrams(?:\s+M1A2)?|stryker|ajax|ascod|"
    r"HIMARS|MLRS|"
    r"patriot|PAC-3|"
    r"NSM|NASAMS|"
    r"CAMM|sea\s+ceptor|"
    r"meteor|ASRAAM|brimstone|AMRAAM|sidewinder|stinger|"
    r"V-22(?:\s+osprey)?"
    r")\b",
    re.IGNORECASE,
)

# ISO-ish country name detection — narrow, based on countries Arkmurus
# actively tracks. Full ISO-3166 is in jurisdiction_inference.py; this
# regex is optimised for the headline shape of FMS / procurement alerts.
_COUNTRY_REGEX = re.compile(
    r"\b("
    r"Denmark|Norway|Sweden|Finland|Iceland|"
    r"UK|United\s+Kingdom|Britain|Scotland|"
    r"Ireland|Germany|France|Italy|Spain|Portugal|Netherlands|Belgium|Luxembourg|"
    r"Poland|Czech|Slovakia|Hungary|Romania|Bulgaria|Greece|Croatia|Slovenia|"
    r"Turkey|Türkiye|"
    r"Switzerland|Austria|"
    r"USA|United\s+States|US|"
    r"Canada|Mexico|Brazil|Argentina|Chile|Colombia|Peru|Venezuela|"
    r"Japan|South\s+Korea|Korea|India|Pakistan|Indonesia|Malaysia|Singapore|Philippines|Thailand|Vietnam|Taiwan|Australia|New\s+Zealand|"
    r"Saudi\s+Arabia|UAE|United\s+Arab\s+Emirates|Qatar|Kuwait|Oman|Bahrain|"
    r"Egypt|Morocco|Algeria|Tunisia|Libya|"
    r"Nigeria|Ghana|Senegal|Ivory\s+Coast|Côte\s+d'Ivoire|"
    r"Kenya|Uganda|Tanzania|Rwanda|Ethiopia|South\s+Africa|"
    r"Angola|Mozambique|Guinea-Bissau|Guinea\s*Bissau|Cabo\s+Verde|"
    r"Israel|Jordan|Lebanon"
    r")\b",
    re.IGNORECASE,
)


def _extract_text(alert: dict | str) -> str:
    if isinstance(alert, str):
        return alert
    if isinstance(alert, dict):
        return " ".join(
            str(alert.get(k, "") or "")
            for k in ("title", "text", "summary", "content", "headline", "snippet", "subject")
        ).strip()
    return ""


def _extract_url(alert: dict | str) -> Optional[str]:
    if isinstance(alert, dict):
        return (alert.get("url") or alert.get("link") or "").strip() or None
    if isinstance(alert, str):
        m = re.search(r"https?://\S+", alert)
        return m.group(0).rstrip(",.;:!?)\"'>") if m else None
    return None


def _detect_prime(text: str, hint: str = "") -> Optional[str]:
    if hint:
        return hint.lower()
    m = _PRIME_REGEX.search(text)
    return m.group(0).lower() if m else None


def _detect_product(text: str, hint: str = "") -> Optional[str]:
    if hint:
        return hint
    m = _PRODUCT_REGEX.search(text)
    return m.group(0) if m else None


def _detect_country(text: str, hint: str = "") -> Optional[str]:
    if hint:
        return hint
    m = _COUNTRY_REGEX.search(text)
    return m.group(0) if m else None


def _build_opportunity_name(
    country: Optional[str],
    product: Optional[str],
    prime: Optional[str],
    url: Optional[str],
) -> str:
    parts: list[str] = []
    if country:
        parts.append(country)
    if product:
        parts.append(product)
    if prime and not product:
        parts.append(prime.title())
    if not parts:
        # Fall back to URL-host extraction or "Unnamed"
        if url:
            host = re.sub(r"^https?://(www\.)?", "", url).split("/", 1)[0]
            parts.append(host)
    base = " — ".join(parts) if parts else "Unnamed intel lead"
    return base[:280]


def _build_markdown_brief(
    *,
    opp_name: str,
    prime: Optional[str],
    product: Optional[str],
    country: Optional[str],
    url: Optional[str],
    source_text: str,
    opportunities: list[dict[str, Any]],
) -> str:
    lines: list[str] = [f"### {opp_name}"]
    if prime or product or country:
        lines.append("")
        lines.append("**Signal fingerprint**")
        if country:
            lines.append(f"- Country: {country}")
        if product:
            lines.append(f"- Product family: {product}")
        if prime:
            lines.append(f"- Prime: {prime.title()}")
    if url:
        lines.append(f"- Source: {url}")
    lines.append("")
    lines.append("**Original signal**")
    lines.append(f"> {source_text[:400]}")
    lines.append("")
    if opportunities:
        lines.append("**Arkmurus-credible sub-contractor angles**")
        lines.append("| Fit | Sub-contractor | Country | Role | Entry path |")
        lines.append("|---|---|---|---|---|")
        for o in opportunities[:8]:
            lines.append(
                f"| {o['fit']} | {o['sub_contractor']} | {o['country']} | "
                f"{o['role']} | {o['entry_path']} |"
            )
    else:
        lines.append("**Arkmurus-credible sub-contractor angles**")
        lines.append(
            "_None surfaced automatically — run `/investigate` on the prime "
            "or search corpus for ecosystem intel._"
        )
    lines.append("")
    lines.append("**Next actions (recommended)**")
    if country and product:
        lines.append(f"1. Pipeline entry with Stage=IDENTIFIED for {country} / {product}")
        lines.append(f"2. Run `/investigate {country} {product} {prime or ''}` for deep DD")
        lines.append(f"3. Flag top 3 sub-contractors into capability-gap watchlist")
    else:
        lines.append("1. Confirm prime + product from primary source before Pipeline entry")
        lines.append("2. Run deep_research on the news URL for full programme context")
    return "\n".join(lines)


async def convert(
    alert: dict | str,
    *,
    push_to_airtable: bool = True,
    prime_hint: str = "",
    product_hint: str = "",
    country_hint: str = "",
) -> dict[str, Any]:
    """Main entry point. See module docstring."""
    text = _extract_text(alert)
    url = _extract_url(alert)
    if not text and not url:
        return {"ok": False, "reason": "empty_alert"}

    prime = _detect_prime(text, prime_hint)
    product = _detect_product(text, product_hint)
    country = _detect_country(text, country_hint)

    opportunities: list[dict[str, Any]] = []
    if prime:
        try:
            from .prime_sub_map import arkmurus_opportunities
            opportunities = arkmurus_opportunities(prime, product or "")
        except Exception as e:
            logger.debug("prime_sub_map lookup failed: %s", e)

    opp_name = _build_opportunity_name(country, product, prime, url)
    brief = _build_markdown_brief(
        opp_name=opp_name,
        prime=prime, product=product, country=country,
        url=url,
        source_text=text,
        opportunities=opportunities,
    )

    airtable_result: dict[str, Any] = {"ok": False, "reason": "skipped"}
    if push_to_airtable:
        try:
            from ..integrations import airtable_pipeline
            notes_parts = [
                f"Source URL: {url}" if url else "",
                f"Original signal: {text[:800]}" if text else "",
                "",
                "Auto-converted from intel alert via "
                "intel/opportunity_converter.py. Review prime/product/"
                "country fields below; run /investigate for deep DD.",
            ]
            airtable_result = await airtable_pipeline.create_opportunity(
                opp_name,
                notes="\n".join(p for p in notes_parts if p is not None),
                # Intentionally NOT setting stage — Pipeline.Stage is a
                # singleSelect with operator-curated options; our PAT
                # can't create new ones. Operator sets Stage in the UI
                # from the existing dropdown (IDENTIFIED / QUALIFIED / etc).
            )
        except Exception as e:
            logger.warning("airtable_pipeline.create_opportunity failed: %s", e)
            airtable_result = {"ok": False, "reason": f"exception:{type(e).__name__}"}

    return {
        "ok": True,
        "opportunity_name": opp_name,
        "prime": prime,
        "product_family": product,
        "country": country,
        "source_url": url,
        "sub_contractor_candidates": opportunities,
        "brief_markdown": brief,
        "airtable": airtable_result,
    }
