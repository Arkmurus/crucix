"""Persist bounded remote failure evidence carried with a cycle sentinel."""
from __future__ import annotations

import argparse
import base64
import zlib
from pathlib import Path


_BEGIN = "__ARIA_FAILURE_BUNDLE_BEGIN__"
_END = "__ARIA_FAILURE_BUNDLE_END__"
_MAX_ENCODED_BYTES = 1_000_000
_MAX_DECODED_BYTES = 512_000


def parse_cycle_observation(observation: str) -> tuple[int, str | None]:
    """Return the cycle rc and required bounded evidence for nonzero outcomes."""
    first, separator, remainder = observation.partition("\n")
    if not separator or not first.strip().isdigit():
        raise ValueError("cycle observation lacks a numeric status sentinel")
    rc = int(first.strip())
    if rc == 0:
        return rc, None
    before, begin, after = remainder.partition(_BEGIN + "\n")
    payload, end, trailing = after.partition("\n" + _END)
    if before.strip() or not begin or not end or trailing.strip():
        raise ValueError("failed cycle observation lacks one bounded evidence bundle")
    if not payload or len(payload) > _MAX_ENCODED_BYTES:
        raise ValueError("failed cycle evidence bundle is empty or exceeds its bound")
    try:
        compressed = base64.b64decode(payload, validate=True)
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        decoded = inflater.decompress(compressed, _MAX_DECODED_BYTES + 1)
        complete = inflater.eof and not inflater.unconsumed_tail
    except (ValueError, zlib.error) as exc:
        raise ValueError("failed cycle evidence bundle is corrupt") from exc
    if len(decoded) > _MAX_DECODED_BYTES or not complete:
        raise ValueError("failed cycle evidence expands beyond its bound")
    return rc, decoded.decode("utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """Validate one observation and persist its failure evidence when required."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--failure-out", required=True, type=Path)
    args = parser.parse_args(argv)
    rc, evidence = parse_cycle_observation(args.input.read_text(encoding="utf-8"))
    if evidence is not None:
        args.failure_out.parent.mkdir(parents=True, exist_ok=True)
        args.failure_out.write_text(evidence, encoding="utf-8", newline="\n")
    print(rc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
