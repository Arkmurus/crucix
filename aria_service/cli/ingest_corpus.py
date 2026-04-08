"""Tiered corpus ingest CLI.

Walks a folder of documents and POSTs each one to the running ARIA service's
/api/aria/corpus/ingest endpoint with explicit tier metadata. This is what
you run *once* (and re-run when you add new documents) to populate ARIA's
proprietary RAG corpus.

Why a CLI rather than a one-shot in-process script
══════════════════════════════════════════════════
The chromadb store lives on the deployed fly.io machine (under /data/aria_rag).
Running a local Python script that imports rag_store would write to your
laptop's filesystem, not the production store. By POSTing to the deployed
service we ingest into the same store ARIA actually queries from at runtime.

Usage
═════
    # Single file
    python -m aria_service.cli.ingest_corpus \\
        --tier D \\
        --source-class "Arkmurus DD report" \\
        --region "Lusophone" \\
        --cplp-relevant \\
        --aria-url https://aria-intel.fly.dev \\
        --token $ARIA_INT_TOKEN \\
        path/to/AIA-due-diligence.pdf

    # Whole folder
    python -m aria_service.cli.ingest_corpus \\
        --tier D \\
        --source-class "Arkmurus templates" \\
        --aria-url https://aria-intel.fly.dev \\
        --token $ARIA_INT_TOKEN \\
        path/to/templates_folder/

Exit codes
══════════
    0 — all files ingested successfully
    1 — at least one file failed (others may still have succeeded)
    2 — bad arguments / no input found / unrecoverable error
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_TIMEOUT_S = 120
SUPPORTED_EXTS = {".pdf", ".docx", ".xlsx", ".xlsm", ".txt", ".md", ".markdown"}


def _guess_mimetype(path: Path) -> str:
    mt, _ = mimetypes.guess_type(str(path))
    if mt:
        return mt
    ext = path.suffix.lower()
    return {
        ".pdf":  "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".txt":  "text/plain",
        ".md":   "text/markdown",
    }.get(ext, "application/octet-stream")


def _post(url: str, body: dict, token: str, timeout: int) -> tuple[int, dict | str]:
    """POST a JSON body and return (status_code, parsed_or_text)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}" if token else "",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body_text or str(e)
    except urllib.error.URLError as e:
        return -1, f"URL error: {e}"
    except Exception as e:
        return -1, f"unexpected error: {e}"


def _collect_files(input_path: Path) -> list[Path]:
    """Walk a path and return all supported files in deterministic order."""
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in SUPPORTED_EXTS else []
    if not input_path.is_dir():
        return []
    found: list[Path] = []
    for p in sorted(input_path.rglob("*")):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            found.append(p)
    return found


def _ingest_one(
    file_path: Path,
    *,
    aria_url: str,
    token: str,
    tier: str,
    source_class: str,
    region: str,
    cplp_relevant: bool,
    confidence: str,
    publication_date: str,
    notes: str,
    timeout: int,
) -> tuple[bool, str]:
    """Ingest a single file. Returns (success, message)."""
    try:
        raw = file_path.read_bytes()
    except Exception as e:
        return False, f"read failed: {e}"

    body = {
        "filename": file_path.name,
        "content_b64": base64.b64encode(raw).decode("ascii"),
        "mimetype": _guess_mimetype(file_path),
        "tier": tier,
        "source_class": source_class,
        "region": region,
        "cplp_relevant": cplp_relevant,
        "confidence": confidence,
        "publication_date": publication_date,
        "notes": notes,
    }
    endpoint = aria_url.rstrip("/") + "/api/aria/corpus/ingest"
    status, payload = _post(endpoint, body, token, timeout)
    if status == 200 and isinstance(payload, dict) and payload.get("ingested"):
        chunks = payload.get("chunks", "?")
        return True, f"ingested {chunks} chunks"
    return False, f"HTTP {status}: {payload!r}"[:300]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m aria_service.cli.ingest_corpus",
        description="Push curated documents into ARIA's RAG store with tier metadata.",
    )
    p.add_argument("input", help="Path to a single file or a folder of files")
    p.add_argument("--tier", required=True, choices=["A", "B", "C", "D"],
                   help="Corpus tier: A=primary, B=secondary, C=live, D=proprietary")
    p.add_argument("--source-class", required=True,
                   help="Publisher / origin label, e.g. 'SIPRI' or 'Arkmurus DD report'")
    p.add_argument("--region", default="",
                   help="Region tag, e.g. 'Africa' / 'Lusophone' / 'MENA'")
    p.add_argument("--cplp-relevant", action="store_true",
                   help="Mark as CPLP-relevant for retrieval boost")
    p.add_argument("--confidence", default="",
                   help="Default confidence tag for the source (CONFIRMED/PROBABLE/...)")
    p.add_argument("--publication-date", default="",
                   help="ISO date string if known")
    p.add_argument("--notes", default="",
                   help="Free-form note about the document")
    p.add_argument("--aria-url", default=os.getenv("ARIA_SERVICE_URL", "http://localhost:8000"),
                   help="Base URL of the ARIA service (defaults to ARIA_SERVICE_URL env)")
    p.add_argument("--token", default=os.getenv("ARIA_INT_TOKEN", ""),
                   help="Bearer token (defaults to ARIA_INT_TOKEN env)")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                   help="Per-request timeout in seconds")
    p.add_argument("--dry-run", action="store_true",
                   help="List files that would be ingested without sending anything")
    args = p.parse_args(argv)

    input_path = Path(args.input).expanduser().resolve()
    files = _collect_files(input_path)
    if not files:
        print(f"ERROR: no supported files found at {input_path}", file=sys.stderr)
        print(f"  Supported extensions: {sorted(SUPPORTED_EXTS)}", file=sys.stderr)
        return 2

    print(f"Found {len(files)} file(s) under {input_path}")
    print(f"Target: {args.aria_url}/api/aria/corpus/ingest")
    print(f"Tier: {args.tier}  Source class: {args.source_class!r}")
    if args.region:
        print(f"Region: {args.region}")
    if args.cplp_relevant:
        print("CPLP-relevant: yes")
    print()

    if args.dry_run:
        for f in files:
            print(f"  [dry-run] {f.relative_to(input_path) if input_path.is_dir() else f.name}")
        return 0

    n_ok = 0
    n_fail = 0
    t_start = time.time()
    for i, f in enumerate(files, 1):
        rel = str(f.relative_to(input_path)) if input_path.is_dir() else f.name
        print(f"[{i}/{len(files)}] {rel} ... ", end="", flush=True)
        ok, msg = _ingest_one(
            f,
            aria_url=args.aria_url,
            token=args.token,
            tier=args.tier,
            source_class=args.source_class,
            region=args.region,
            cplp_relevant=args.cplp_relevant,
            confidence=args.confidence,
            publication_date=args.publication_date,
            notes=args.notes,
            timeout=args.timeout,
        )
        if ok:
            n_ok += 1
            print(f"OK — {msg}")
        else:
            n_fail += 1
            print(f"FAIL — {msg}")

    elapsed = time.time() - t_start
    print()
    print(f"Done. {n_ok} succeeded, {n_fail} failed, in {elapsed:.1f}s")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
