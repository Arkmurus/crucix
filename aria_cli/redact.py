"""R-F2796 (D9) — secret redaction for ARIA CLI terminal output.

WHY THIS EXISTS
───────────────
A customer's terminal transcript is effectively published: it gets
screenshotted, pasted into support tickets, and shared with colleagues. So
anything the CLI prints must be treated as public. Before this module, `aria`
running `printenv`, `flyctl secrets list`, `cat .env`, or any command that
echoed a token rendered it verbatim to the screen — `_sanitize_output()`
(R-F2093) stripped control characters but nothing else.

This is the same failure CLASS as R-F2705, where a real key leak on the aria-wa
tier produced `services/wa-listener/log-redact.mjs`. The CLI never got the
equivalent. The key-name vocabulary below is deliberately mirrored from that
module so the two tiers agree on what counts as a secret.

DESIGN — precision over aggression
──────────────────────────────────
Over-redaction is a real cost, not a safe default. A CLI that mangles git SHAs,
image digests, UUIDs and base64 test fixtures teaches the customer to distrust
its output, and hiding the one line an engineer needs is its own failure mode.
So there is NO generic "looks random, redact it" rule. Two precise nets only:

  1. NAME-ANCHORED — `SOMETHING_SECRET=<value>` / `token: <value>`. High
     precision because the key name declares the intent. The name is KEPT and
     only the value is replaced, so the human still learns which credential was
     involved.
  2. SHAPE-ANCHORED — a small set of vendor token formats with effectively zero
     false-positive rate (`sk-ant-`, `sk-`/`sk_live_`, `ghp_`, `github_pat_`,
     `xox[baprs]-`, `AKIA`, `fm2_`, JWTs, Telegram bot tokens) plus credentials
     embedded in a URL.

Values are redacted WHOLESALE, never partially. R-F1286 removed prefix/suffix
token disclosure elsewhere in this repo for precisely this reason: a partial
token narrows a brute force and buys nothing.

The negative tests in test_rf2796_terminal_secret_redaction.py are as
load-bearing as the positive ones — if you widen a pattern here, run them.
"""
from __future__ import annotations

import re

REDACTED = "«redacted»"

# Mirrors SECRET_KEY_RE in services/wa-listener/log-redact.mjs (R-F2705) so the
# CLI and the WA tier agree on the credential vocabulary. Anchored to a whole
# key token, not a substring of arbitrary prose.
_SECRET_NAME = (
    r"(?:priv(?:ate)?[_-]?key|secret|password|passwd|pwd|token|credential|creds?"
    r"|api[_-]?key|apikey|access[_-]?key|auth|bearer|cookie|session[_-]?key"
    r"|mnemonic|seed|passphrase|signing[_-]?key)"
)

# 1. NAME-ANCHORED: KEY=value / KEY: value / KEY = "value".
#    The value runs to end-of-line or the closing quote. We keep group 1 (the
#    name + separator) and replace the value.
_ASSIGNMENT_RE = re.compile(
    rf"""(
        (?:^|\s|\bexport\s+)            # line start, whitespace, or `export `
        [A-Za-z0-9_.\-]*{_SECRET_NAME}[A-Za-z0-9_.\-]*   # a key containing a secret word
        \s*[:=]\s*                      # separator
        (?P<q>["']?)                    # optional opening quote
    )
    (?P<val>[^\s"']{{4,}})              # the value (>=4 chars, no spaces/quotes)
    """,
    re.IGNORECASE | re.VERBOSE | re.MULTILINE,
)

# 2. SHAPE-ANCHORED vendor tokens. Each prefix is distinctive enough that a
#    match is a credential essentially by definition.
_TOKEN_SHAPES = [
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}"),                      # Anthropic
    re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{8,}"),                     # OpenAI project
    re.compile(r"\bsk_live_[A-Za-z0-9]{8,}"),                        # Stripe live
    re.compile(r"\bsk_test_[A-Za-z0-9]{8,}"),                        # Stripe test
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),                            # OpenAI classic
    re.compile(r"\bghp_[A-Za-z0-9]{16,}"),                           # GitHub PAT
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),                   # GitHub fine-grained
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),                  # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                             # AWS access key id
    re.compile(r"\bfm2_[A-Za-z0-9+/=_\-]{16,}"),                     # Fly.io
    re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_\-]{30,}"),                 # Telegram bot token
    # JWT: three base64url segments. The `eyJ` prefix is the encoded `{"` of a
    # JOSE header, which makes this near-unambiguous.
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}"),
]

# 3. Credentials embedded in a URL — scheme://user:PASSWORD@host. The host is
#    kept because it is not the secret and is usually the useful part.
_URL_CRED_RE = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:)([^\s@/]+)(@)")

# 4. `Authorization: Bearer <token>` / `Bearer <token>` in prose.
_BEARER_RE = re.compile(r"\b(Bearer\s+)([A-Za-z0-9._\-+/=]{8,})", re.IGNORECASE)


def redact_secrets(text: str, *, mode: str = "full") -> str:
    """Replace credential material in `text` with :data:`REDACTED`.

    Two modes, because the two things the CLI prints have different contracts:

    ``full`` (default) — for COMMAND/TOOL output. This is the environment
        talking back: `printenv`, `cat .env`, `flyctl secrets list`. Everything
        that looks like a credential goes, name-anchored assignments included.

    ``vendor_only`` — for ASSISTANT prose. Blanket redaction here would break a
        legitimate flow: if the customer asks ARIA to GENERATE a value ("give me
        a JWT_SECRET"), they must be able to read the answer, and a name-anchored
        rule would blank exactly the thing they asked for. So assistant text is
        scrubbed only of THIRD-PARTY vendor tokens (`sk-ant-`, `ghp_`, JWTs,
        AWS keys…), which ARIA has no legitimate reason to emit and which, if
        present, were read out of the customer's environment rather than
        composed. Embedded URL credentials and `Bearer` headers are also
        scrubbed for the same reason.

    Non-mutating, idempotent, and safe on ``None``/empty input (the CLI calls it
    on whatever a subprocess emitted, which is arbitrary bytes-turned-str).
    """
    if not text:
        return text
    try:
        out = text
        if mode == "full":
            out = _ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", out)
        out = _URL_CRED_RE.sub(lambda m: f"{m.group(1)}{REDACTED}{m.group(3)}", out)
        out = _BEARER_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", out)
        for pat in _TOKEN_SHAPES:
            out = pat.sub(REDACTED, out)
        return out
    except Exception:
        # Belt and braces: if a pattern ever misbehaves on pathological input,
        # returning the ORIGINAL would print the secret. Withhold the chunk
        # instead — failing closed is the whole point of this module.
        return f"{REDACTED} (redaction error — output withheld)"
