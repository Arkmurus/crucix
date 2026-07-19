"""R-F2796 (D9) — the ARIA CLI must not print customer secrets to the terminal.

THE CONTRACT (operator directive 2026-07-19: "mirror what you offer to clients")
--------------------------------------------------------------------------------
A customer's terminal transcript gets screenshotted, pasted into tickets, and
shared. Anything ARIA prints there is effectively published. So the CLI's output
contract is: untrusted output is rendered for the human, but credential material
is replaced before it ever reaches the screen.

THE GAP THIS CLOSES (proven by execution before the fix)
--------------------------------------------------------
`_sanitize_output()` (R-F2093) already guards every untrusted-output site in
cli.py — the error path (:470), tool result rendering (:1403, :1418, :1447) and
live streaming command output (:1544). But it only stripped control characters
and normalised line endings. Running it over real credential shapes returned them
VERBATIM:

    BRAVE_SEARCH_API_KEY=BSA1a2b3c4d5e6f7g8h9i0jKlMnOpQrSt
    export ANTHROPIC_API_KEY=sk-ant-api03-AbCdEf123456789_XyZ
    Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def
    postgres://user:s3cr3tpw@db.internal:5432/aria

So `aria` running `printenv`, `flyctl secrets list`, `cat .env`, or any command
that echoes a token printed it straight to the customer's screen.

This is the same failure CLASS as R-F2705, where a real key leak on the aria-wa
tier produced `log-redact.mjs`. The CLI never got the equivalent. The key-name
vocabulary here is deliberately mirrored from that module so the two tiers agree
on what counts as a secret.

DESIGN NOTE — why not "redact anything that looks random"
---------------------------------------------------------
Over-redaction is a real cost: a CLI that mangles git SHAs, hashes, UUIDs and
base64 test fixtures teaches the customer to distrust its output, and hiding the
thing an engineer needs is its own failure. So redaction is deliberately
PRECISE — name-anchored assignments plus unambiguous well-known token shapes —
and the negative tests below are as load-bearing as the positive ones.

Values are redacted WHOLESALE, never partially. R-F1286 removed prefix/suffix
disclosure elsewhere in this repo for exactly this reason: a partial token
narrows a brute force. The KEY NAME is preserved so the human still learns which
credential was involved.
"""

import pytest

from aria_cli.redact import REDACTED, redact_secrets


# ── MUST redact ─────────────────────────────────────────────────────────────

SECRET_LINES = [
    "BRAVE_SEARCH_API_KEY=BSA1a2b3c4d5e6f7g8h9i0jKlMnOpQrSt",
    "export ANTHROPIC_API_KEY=sk-ant-api03-AbCdEf123456789_XyZ",
    "ARIA_API_TOKEN=TPWspa3T5esw2YVh5Y7wemddnSSiLQAxZUz120u5uvk",
    "JWT_SECRET=averylongsecretvaluethatmustnotbeprinted123",
    "  DATABASE_PASSWORD: hunter2hunter2hunter2",
    'api_key = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"',
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
    "STRIPE_SECRET_KEY=sk_live_EXAMPLENOTREAL1",
    "GITHUB_TOKEN=ghp_EXAMPLENOTAREALTOKEN1",
    "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "FLY_API_TOKEN=fm2_abcdefghijklmnopqrstuvwxyz",
    "TELEGRAM_BOT_TOKEN=123456789:AAEhBOweik6ad9rmnvbCDEfghIJKlmnop",
]


@pytest.mark.parametrize("line", SECRET_LINES)
def test_credential_value_is_removed(line: str):
    out = redact_secrets(line)
    # The secret VALUE must be gone…
    value = line.split("=", 1)[-1].split(":", 1)[-1].strip().strip('"')
    assert value not in out, f"secret value survived redaction: {out!r}"
    assert REDACTED in out, f"expected a redaction marker in {out!r}"


@pytest.mark.parametrize("line", SECRET_LINES)
def test_key_name_is_preserved(line: str):
    """The human must still learn WHICH credential was involved."""
    out = redact_secrets(line)
    first_token = line.strip().split("=")[0].split(":")[0].replace("export ", "").strip()
    if first_token and first_token.isupper():
        assert first_token in out, f"key name lost: {out!r}"


def test_url_embedded_credentials_are_removed():
    out = redact_secrets("postgres://user:s3cr3tpw@db.internal:5432/aria")
    assert "s3cr3tpw" not in out
    assert "db.internal" in out, "host must survive — it is not the secret"


def test_bare_jwt_is_removed_even_without_a_key_name():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NSJ9.QWxhZGRpbjpvcGVu"
    out = redact_secrets(f"got token {jwt} back")
    assert jwt not in out
    assert REDACTED in out


def test_multiline_output_redacts_every_occurrence():
    text = "\n".join([
        "PATH=/usr/bin",
        "ARIA_API_TOKEN=TPWspa3T5esw2YVh5Y7wemddnSSiLQAxZUz120u5uvk",
        "HOME=/root",
        "ANTHROPIC_API_KEY=sk-ant-api03-secretsecretsecret",
    ])
    out = redact_secrets(text)
    assert "TPWspa3T5esw2YVh5Y7wemddnSSiLQAxZUz120u5uvk" not in out
    assert "sk-ant-api03-secretsecretsecret" not in out
    assert "/usr/bin" in out and "/root" in out, "non-secret env must survive"


# ── MUST NOT redact (over-redaction is its own failure) ─────────────────────

BENIGN = [
    "commit 8458f054e3b1c2d4a5f6978012345678abcdef90",          # git sha
    "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca4",  # digest
    "run_id=dd_c39503fa4c93",
    "PATH=/usr/local/bin:/usr/bin:/bin",
    "NODE_ENV=production",
    "version 2512 -> 2513",
    "550e8400-e29b-41d4-a716-446655440000",                      # uuid
    "https://aria-intel.fly.dev/health/live",
    "15 failed, 1617 passed, 1 skipped",
    "build_rev=R-F2791+R-F2792 · sha 12a45364",
    "user_id=acorrea@arkmurus.com",
    "ARIA_SERVICE_URL=https://aria-intel.fly.dev",               # a URL, not a credential
]


@pytest.mark.parametrize("line", BENIGN)
def test_benign_output_is_untouched(line: str):
    assert redact_secrets(line) == line, "over-redaction mangles legitimate output"


def test_empty_and_none_safe():
    assert redact_secrets("") == ""
    assert redact_secrets(None) is None  # type: ignore[arg-type]


def test_redaction_is_idempotent():
    once = redact_secrets("ARIA_API_TOKEN=TPWspa3T5esw2YVh5Y7wemddnS")
    assert redact_secrets(once) == once, "re-redacting must not compound"


# ── CAPABILITY: the real terminal-output path ───────────────────────────────

def test_cli_sanitize_output_redacts_secrets():
    """Drive the ACTUAL function every untrusted-output site in cli.py calls.

    This is the check that matters: _sanitize_output is the single choke point
    for the error path, tool-result rendering and live streamed command output.
    If redaction is not wired into IT, it is not wired into the product.
    """
    from aria_cli.cli import _sanitize_output

    out = _sanitize_output("ANTHROPIC_API_KEY=sk-ant-api03-AbCdEf123456789_XyZ")
    assert "sk-ant-api03-AbCdEf123456789_XyZ" not in out
    assert REDACTED in out


def test_cli_sanitize_output_still_strips_control_chars():
    """R-F2093's original contract must survive the addition."""
    from aria_cli.cli import _sanitize_output

    out = _sanitize_output("hello\x07\x1b[31mworld\r\ndone")
    assert "\x07" not in out
    assert "\r" not in out
    assert "hello" in out and "done" in out


# ── ASSISTANT PROSE: vendor_only mode ───────────────────────────────────────
# A second leg of the same contract. The model can quote a secret it just read
# out of the customer's environment, and that reaches the terminal via
# _render_markdown. But blanket redaction there would break a legitimate flow.

def test_vendor_only_still_removes_third_party_tokens():
    """A key the model read out of the environment must not reach the screen."""
    for secret in [
        "sk-ant-api03-AbCdEf123456789_XyZ",
        "ghp_EXAMPLENOTAREALTOKEN1",
        "AKIAIOSFODNN7EXAMPLE",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.QWxhZGRpbjpvcGVu",
    ]:
        out = redact_secrets(f"I found {secret} in your config", mode="vendor_only")
        assert secret not in out, f"vendor token survived: {out!r}"


def test_vendor_only_preserves_a_value_the_customer_ASKED_for():
    """The legitimate case blanket redaction would have broken.

    "Generate me a JWT_SECRET" must produce a readable answer. This is why
    assistant prose uses vendor_only rather than full.
    """
    generated = "JWT_SECRET=9f2c1ab77e5d4c3b8a06f1e2d3c4b5a69f2c1ab77e5d4c3b"
    out = redact_secrets(f"Here is a fresh value:\n{generated}", mode="vendor_only")
    assert generated in out, "the customer must be able to read what they asked ARIA to generate"


def test_full_mode_DOES_redact_that_same_assignment():
    """…but the same text coming back from `printenv` is redacted."""
    line = "JWT_SECRET=9f2c1ab77e5d4c3b8a06f1e2d3c4b5a69f2c1ab77e5d4c3b"
    assert REDACTED in redact_secrets(line, mode="full")


def test_vendor_only_still_scrubs_url_credentials_and_bearer():
    out = redact_secrets("try postgres://u:pw123456@host/db", mode="vendor_only")
    assert "pw123456" not in out
    out2 = redact_secrets("send Authorization: Bearer abcdef1234567890", mode="vendor_only")
    assert "abcdef1234567890" not in out2
