"""R-F2796 / R-F2804 — secret redaction for ARIA CLI terminal output.

WHY THIS EXISTS
───────────────
A customer's terminal transcript is effectively published: it gets
screenshotted, pasted into support tickets, and shared with colleagues. So
anything the CLI prints must be treated as public. Before R-F2796, `aria`
running `printenv`, `flyctl secrets list`, `cat .env`, or any command that
echoed a token rendered it verbatim to the screen — `_sanitize_output()`
(R-F2093) stripped control characters but nothing else.

Same failure CLASS as R-F2705, where a real key leak on the aria-wa tier
produced `services/wa-listener/log-redact.mjs`. The key-name vocabulary below is
deliberately mirrored from that module so the two tiers agree on what counts as
a secret.

DESIGN — precision over aggression
──────────────────────────────────
Over-redaction is a real cost, not a safe default. A CLI that mangles git SHAs,
image digests, UUIDs and base64 fixtures teaches the customer to distrust its
output, and hiding the one line an engineer needs is its own failure mode. So
there is NO generic "looks random, redact it" rule. Four precise nets:

  1. NAME-ANCHORED  — `SOMETHING_SECRET=<value>` / `"api_key": "<value>"`. The
     key name declares the intent. The name is KEPT and only the value replaced,
     so the human still learns which credential was involved.
  2. SHAPE-ANCHORED — vendor token formats with ~zero false-positive rate.
  3. PEM            — private-key blocks, which have no key=value shape at all.
  4. URL / Bearer   — credentials embedded in a URL or an Authorization header.

Values are redacted WHOLESALE, never partially — R-F1286 removed prefix/suffix
token disclosure elsewhere in this repo because a partial token narrows a brute
force and buys nothing.

The negative tests are as load-bearing as the positive ones. If you widen a
pattern here, run test_rf2804_redaction_hardening.py AND
test_rf2796_terminal_secret_redaction.py.
"""
from __future__ import annotations

import re

REDACTED = "«redacted»"

# Mirrors SECRET_KEY_RE in services/wa-listener/log-redact.mjs (R-F2705).
_SECRET_NAME = (
    r"(?:priv(?:ate)?key|secret|password|passwd|pwd|token|credential|creds?"
    r"|apikey|accesskey|auth|bearer|cookie|sessionkey"
    r"|mnemonic|seed|passphrase|signingkey)"
)
_SECRET_NAME_RE = re.compile(rf"^{_SECRET_NAME}$", re.IGNORECASE)

# A key whose LAST component names a location or a description, not the
# credential itself: `--token-file`, `key_path`, `password_hint`, `token_count`.
_NON_SECRET_SUFFIXES = frozenset({
    "file", "path", "dir", "hint", "name", "id", "count", "len", "length",
    "type", "url", "uri", "expiry", "ttl", "enabled", "present", "status",
})


def _key_is_secret(key: str) -> bool:
    """True when a credential word is a WHOLE component of `key`.

    R-F2804 — the old rule spliced the credential alternation between two
    `[A-Za-z0-9_.\\-]*` stars, which made it a SUBSTRING match. That destroyed
    legitimate output: `Authorization=none` (hiding that auth is DISABLED —
    actively misleading mid-debug), `secretariat=OpenAI`, `--token-file=/etc/x`,
    `tokens=1024`. Splitting on the separators real key names use and matching
    whole components keeps `AWS_SECRET_ACCESS_KEY` and `api-key` while dropping
    those.
    """
    parts = [p for p in re.split(r"[_.\-]+", key or "") if p]
    if not parts:
        return False
    if parts[-1].lower() in _NON_SECRET_SUFFIXES:
        return False
    if any(_SECRET_NAME_RE.match(p) for p in parts):
        return True
    # Multi-word credential names split across the separator: BRAVE_SEARCH_API_KEY
    # -> [brave, search, api, key], where neither `api` nor `key` matches alone
    # but the ADJACENT PAIR `apikey` does. Same for access_key, private_key,
    # session_key, signing_key. Pairs only — joining everything would resurrect
    # the substring matching this function exists to avoid.
    return any(
        _SECRET_NAME_RE.match(parts[i] + parts[i + 1])
        for i in range(len(parts) - 1)
    )


# ── 1. NAME-ANCHORED ────────────────────────────────────────────────────────
# R-F2804 — the key is captured as ONE token and tested in Python (above),
# instead of being spliced into the regex between two overlapping stars.
#
# The old shape had two faults with a single root cause. The stars overlapped
# the alternation's own character class, so for each start position the engine
# tried every (star-length x alternative-offset) pair — O(n^2), and `re` has no
# timeout, so it HANGS rather than raising and the fail-closed `except` never
# fires. Measured before this fix: 1200 chars 0.004s -> 9600 chars 0.195s (4x
# per doubling) ≈ 35 minutes at 1MB, on a function that runs over arbitrary
# subprocess output. Capturing once fixes the blowup AND the over-matching.
#
# Two value alternatives, quoted first: a quoted value runs to its CLOSING quote
# so `PASSWORD="hunter2 correct horse"` is removed whole. Previously the value
# class stopped at the first space, leaking three quarters of the passphrase
# while LOOKING redacted — which is worse than not redacting at all.
_ASSIGNMENT_RE = re.compile(
    r"""(?P<lead>^|[\s{,\[])
        (?P<qk>["']?)
        (?P<key>[A-Za-z0-9_.\-]{1,64})
        (?P=qk)
        (?P<sep>[ \t]*[:=][ \t]*)
        (?:
            (?P<qv>["'])(?P<qval>[^"'\r\n]{4,})(?P=qv)
          | (?P<val>[^\s"',}\]]{4,})
        )
    """,
    re.VERBOSE | re.MULTILINE,
)


def _sub_assignment(m: "re.Match") -> str:
    if not _key_is_secret(m.group("key")):
        return m.group(0)
    qv = m.group("qv")
    if qv:
        return (f"{m.group('lead')}{m.group('qk')}{m.group('key')}{m.group('qk')}"
                f"{m.group('sep')}{qv}{REDACTED}{qv}")
    return (f"{m.group('lead')}{m.group('qk')}{m.group('key')}{m.group('qk')}"
            f"{m.group('sep')}{REDACTED}")


# ── 2. SHAPE-ANCHORED vendor tokens ─────────────────────────────────────────
_TOKEN_SHAPES = [
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}"),                       # Anthropic
    re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{8,}"),                      # OpenAI project
    re.compile(r"\bsk_live_[A-Za-z0-9]{8,}"),                         # Stripe live
    re.compile(r"\bsk_test_[A-Za-z0-9]{8,}"),                         # Stripe test
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),                             # OpenAI classic
    re.compile(r"\bghp_[A-Za-z0-9]{16,}"),                            # GitHub PAT
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),                    # GitHub fine-grained
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),                   # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                              # AWS access key id
    re.compile(r"\bfm2_[A-Za-z0-9+/=_\-]{16,}"),                      # Fly.io
    re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_\-]{30,}"),                  # Telegram bot token
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}"),  # JWT
]

# ── 3. PEM private-key blocks ───────────────────────────────────────────────
# R-F2804 — `cat ~/.ssh/id_rsa` and service-account JSON printed the whole body:
# there is no `[:=]` for the name rule to anchor on and no vendor prefix.
# Matches both real newlines and the literal `\n` escapes a key carries when
# embedded in JSON. CERTIFICATE blocks are deliberately NOT matched — they are
# public material and redacting them is pure noise.
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
    r".*?"
    r"-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)

# ── 4. URL-embedded credentials and Bearer headers ──────────────────────────
# The URL host is kept — it is not the secret and is usually the useful part.
_URL_CRED_RE = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:)([^\s@/]+)(@)")
_BEARER_RE = re.compile(r"\b(Bearer\s+)([A-Za-z0-9._\-+/=]{8,})", re.IGNORECASE)


def redact_secrets(text: str, *, mode: str = "full") -> str:
    """Replace credential material in `text` with :data:`REDACTED`.

    Two modes, because the two things the CLI prints have different contracts:

    ``full`` (default) — COMMAND/TOOL output and the command line itself. This is
        the environment talking back: `printenv`, `cat .env`, `flyctl secrets
        list`. Everything credential-shaped goes, name-anchored included.

    ``vendor_only`` — ASSISTANT prose. Blanket redaction would break a legitimate
        flow: if the customer asks ARIA to GENERATE a value ("give me a
        JWT_SECRET"), they must be able to read the answer, and a name-anchored
        rule would blank exactly what they asked for. So assistant text is
        scrubbed of third-party vendor tokens, PEM blocks, URL credentials and
        Bearer headers — none of which ARIA has a legitimate reason to compose;
        if present, they were read out of the customer's environment.

    Non-mutating, idempotent, and safe on ``None``/empty input (it is called on
    whatever a subprocess emitted, which is arbitrary bytes-turned-str).
    """
    if not text:
        return text
    try:
        out = text
        # PEM first: a key body can contain `:` and would otherwise be chewed by
        # the assignment rule into something unrecognisable but still leaky.
        out = _PEM_RE.sub(REDACTED, out)
        if mode == "full":
            out = _ASSIGNMENT_RE.sub(_sub_assignment, out)
        out = _URL_CRED_RE.sub(lambda m: f"{m.group(1)}{REDACTED}{m.group(3)}", out)
        out = _BEARER_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", out)
        for pat in _TOKEN_SHAPES:
            out = pat.sub(REDACTED, out)
        return out
    except Exception:
        # If a pattern ever misbehaves on pathological input, returning the
        # ORIGINAL would print the secret. Withhold the chunk instead — failing
        # closed is the whole point of this module.
        return f"{REDACTED} (redaction error — output withheld)"
