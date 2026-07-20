"""R-F2804 — hardening the R-F2796 CLI redaction against five real gaps.

An adversarial review of R-F2796 found five defects, each REPRODUCED by probe
before this fix (recorded per-test below). R-F2796's contract stands — a terminal
transcript is effectively published, so credential material must never reach the
screen — but its implementation missed the primary path and several common shapes.

  A1 (HIGH)  the STREAMING path bypassed redaction entirely
  A2 (HIGH)  PEM blocks and JSON-shaped credentials were untouched
  A3 (MED)   the run() tool-call echo printed AND logged the command unredacted
  A4 (MED)   quoted values with spaces were only partially redacted
  A5 (MED)   the assignment regex was O(n^2) on adversarial input, and — same
             root cause — matched key names as SUBSTRINGS, so it destroyed
             legitimate output

The negative tests remain as load-bearing as the positive ones: a redactor that
mangles ordinary output teaches the operator to distrust it, and `Authorization=none`
being hidden during a security debug is its own failure.
"""

import time

import pytest

from aria_cli.redact import REDACTED, redact_secrets


# ── A2: PEM blocks ──────────────────────────────────────────────────────────

PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAx7Vk9SECRETBODYLINE1\n"
    "MIIEowIBAAKCAQEAx7Vk9SECRETBODYLINE2\n"
    "-----END RSA PRIVATE KEY-----"
)


def test_pem_private_key_block_is_removed():
    """`cat ~/.ssh/id_rsa` printed the entire key body verbatim."""
    out = redact_secrets(f"here it is:\n{PEM}\ndone")
    assert "SECRETBODYLINE1" not in out
    assert "SECRETBODYLINE2" not in out
    assert REDACTED in out
    assert "here it is:" in out and "done" in out, "surrounding output must survive"


@pytest.mark.parametrize("label", [
    "RSA PRIVATE KEY", "PRIVATE KEY", "EC PRIVATE KEY",
    "OPENSSH PRIVATE KEY", "ENCRYPTED PRIVATE KEY",
])
def test_all_pem_private_key_flavours_are_removed(label: str):
    block = f"-----BEGIN {label}-----\nMIIBODYSECRET\n-----END {label}-----"
    assert "MIIBODYSECRET" not in redact_secrets(block)


def test_certificate_block_is_NOT_redacted():
    """A CERTIFICATE is public material — redacting it is pure noise."""
    cert = "-----BEGIN CERTIFICATE-----\nMIIDpubliccertbody\n-----END CERTIFICATE-----"
    assert redact_secrets(cert) == cert


# ── A2: JSON-shaped credentials ─────────────────────────────────────────────

def test_json_credential_value_is_removed():
    """`cat service-account.json` / `gcloud --format=json` leaked whole.

    _ASSIGNMENT_RE's prefix could not cross a quote, so a JSON key never matched.
    """
    out = redact_secrets('{\n  "api_key": "abcdef1234567890abcdef",\n  "region": "eu-west-1"\n}')
    assert "abcdef1234567890abcdef" not in out
    assert "eu-west-1" in out, "non-secret JSON fields must survive"


def test_json_private_key_with_escaped_newlines_is_removed():
    blob = '{"private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEvQIBADSECRET\\n-----END PRIVATE KEY-----"}'
    out = redact_secrets(blob)
    assert "MIIEvQIBADSECRET" not in out


def test_json_non_secret_keys_survive():
    blob = '{"client_email": "a@b.iam", "project_id": "my-project", "type": "service_account"}'
    assert redact_secrets(blob) == blob


# ── A4: quoted values containing spaces ─────────────────────────────────────

def test_quoted_value_with_spaces_is_fully_removed():
    """Previously leaked everything after the first space — AND looked redacted,
    so the operator would trust it."""
    out = redact_secrets('PASSWORD="hunter2 correct horse battery"')
    for word in ("hunter2", "correct", "horse", "battery"):
        assert word not in out, f"{word!r} survived: {out!r}"
    assert REDACTED in out


def test_single_quoted_value_with_spaces_is_fully_removed():
    out = redact_secrets("SMTP_PASSWORD='my pass phrase here'")
    assert "pass phrase" not in out


def test_text_after_the_closing_quote_survives():
    out = redact_secrets('PASSWORD="a b c" && echo done')
    assert "echo done" in out, "only the quoted value is the secret"


# ── A5: over-redaction (the negative half of the contract) ──────────────────

@pytest.mark.parametrize("line", [
    "Authorization=none",          # hides that auth is DISABLED — actively misleading
    "Authorization: none",
    "secretariat=OpenAI",          # 'secret' as a substring of an ordinary word
    "--token-file=/etc/aria/x",    # a filesystem path, not a credential
    "tokens=1024",
    "PATH=/usr/local/bin:/usr/bin",
    "NODE_ENV=production",
    "password_hint: check the sticky note",
])
def test_ordinary_output_is_not_mangled(line: str):
    assert redact_secrets(line) == line, "over-redaction destroys legitimate output"


@pytest.mark.parametrize("line,secret", [
    ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCY", "wJalrXUtnFEMIK7MDENGbPxRfiCY"),
    ("api-key: abcdef1234567890abcd", "abcdef1234567890abcd"),
    ("ARIA_API_TOKEN=TPWspa3T5esw2YVh5Y7wemddnSSiLQ", "TPWspa3T5esw2YVh5Y7wemddnSSiLQ"),
    ("db_password=hunter2hunter2", "hunter2hunter2"),
])
def test_real_credentials_are_still_removed(line: str, secret: str):
    """Tightening the key match must not stop catching genuine secrets."""
    out = redact_secrets(line)
    assert secret not in out, f"secret survived: {out!r}"


# ── A5: ReDoS ───────────────────────────────────────────────────────────────

def test_adversarial_input_does_not_blow_up():
    """The regex was O(n^2) per start position; `re` has no timeout, so this is a
    HANG, not an exception — the fail-closed `except` never fires.

    It runs on arbitrary subprocess output (a repo file, a fetched page, a log),
    so the attacker is anyone who can land a file in a repo ARIA reads.
    Measured before the fix: 1200 chars 0.004s -> 9600 chars 0.195s (4x per
    doubling), i.e. ~35 minutes at 1MB.
    """
    payload = "secret" * 20_000          # 120 KB, no separator anywhere
    t0 = time.time()
    redact_secrets(payload)
    elapsed = time.time() - t0
    assert elapsed < 2.0, f"redaction took {elapsed:.1f}s on 120KB — quadratic blowup"


def test_large_benign_output_is_fast():
    payload = ("PATH=/usr/bin\nNODE_ENV=production\nrunning tests...\n" * 5_000)
    t0 = time.time()
    out = redact_secrets(payload)
    assert time.time() - t0 < 2.0
    assert "running tests..." in out


# ── still-true R-F2796 behaviour (regression guard) ────────────────────────

def test_rf2796_contract_preserved():
    assert REDACTED in redact_secrets("ANTHROPIC_API_KEY=sk-ant-api03-AbCdEf123456789")
    assert "s3cr3tpw" not in redact_secrets("postgres://user:s3cr3tpw@db.internal:5432/aria")
    assert redact_secrets("") == ""
    assert redact_secrets(None) is None  # type: ignore[arg-type]
    once = redact_secrets("ARIA_API_TOKEN=TPWspa3T5esw2YVh5Y7wemddnS")
    assert redact_secrets(once) == once, "idempotent"


def test_vendor_only_mode_still_spares_a_generated_value():
    generated = "JWT_SECRET=9f2c1ab77e5d4c3b8a06f1e2d3c4b5a69f2c1ab7"
    assert generated in redact_secrets(f"Here you go:\n{generated}", mode="vendor_only")
    assert REDACTED in redact_secrets(generated, mode="full")


def test_vendor_only_still_removes_a_pem_block():
    """A PEM the model read out of the environment is never something ARIA
    legitimately composes, so it goes even in vendor_only."""
    assert "SECRETBODYLINE1" not in redact_secrets(PEM, mode="vendor_only")


# ── A1 (HIGH): the STREAMING path — the primary path, previously unguarded ──
# agent.py:579 ALWAYS passes on_delta=ui.stream_delta; ui.assistant() (the only
# _render_markdown caller) runs ONLY when the provider did NOT stream
# (agent.py:591). So R-F2796's assistant-prose redaction never ran in the case
# that actually happens. These drive the REAL AgentUI.stream_delta.

import contextlib
import io as _io


def _ui(anchored: bool):
    from aria_cli import cli
    # TerminalUI is the real implementation (cli.py). `AgentUI` is the PROTOCOL
    # in agent.py that it satisfies — instantiating that one gets you nothing.
    u = cli.TerminalUI(auto_approve=True, interactive=False, color=cli._Color(enabled=False))
    u.anchored = anchored
    u._can_animate = False
    u._needs_leading_newline = False
    return u


def _stream(ui, chunks) -> str:
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        for c in chunks:
            ui.stream_delta(c)
        ui.stream_end()
    return buf.getvalue()


@pytest.mark.parametrize("anchored", [True, False])
def test_streamed_secret_never_reaches_stdout(anchored: bool):
    """The exact live case: the model quotes a key it read from the environment."""
    out = _stream(_ui(anchored), [
        "Your key is ", "sk-ant-api03-", "STREAMEDSECRET12345", " — keep it safe\n",
    ])
    assert "STREAMEDSECRET12345" not in out, f"secret reached the terminal: {out!r}"
    assert REDACTED in out
    assert "keep it safe" in out, "surrounding prose must still render"


@pytest.mark.parametrize("anchored", [True, False])
def test_secret_split_across_token_boundaries_is_caught(anchored: bool):
    """A tokenizer splits mid-string, so a secret routinely straddles deltas.

    This is why redaction must happen at LINE granularity — per-chunk redaction
    would see only fragments and match nothing.
    """
    out = _stream(_ui(anchored), list("sk-ant-api03-SPLITSECRETVALUE9\n"))
    assert "SPLITSECRETVALUE9" not in out, f"straddling secret leaked: {out!r}"


@pytest.mark.parametrize("anchored", [True, False])
def test_secret_with_no_trailing_newline_is_caught_at_stream_end(anchored: bool):
    """The tail is where a final secret lives; without this, omitting a newline
    would trivially escape the stream_delta guard."""
    out = _stream(_ui(anchored), ["token is sk-ant-api03-TAILSECRET4242"])
    assert "TAILSECRET4242" not in out, f"tail secret leaked: {out!r}"


@pytest.mark.parametrize("anchored", [True, False])
def test_ordinary_streamed_prose_is_unchanged(anchored: bool):
    out = _stream(_ui(anchored), ["Here are the results:\n", "15 passed, 0 failed\n"])
    assert "Here are the results:" in out
    assert "15 passed, 0 failed" in out


def test_streaming_uses_vendor_only_so_a_generated_value_survives():
    """A customer asking ARIA to GENERATE a secret must be able to read it."""
    out = _stream(_ui(False), ["JWT_SECRET=9f2c1ab77e5d4c3b8a06f1e2d3c4b5a6\n"])
    assert "9f2c1ab77e5d4c3b8a06f1e2d3c4b5a6" in out


# ── A3 (MED): the tool-call echo printed AND logged the command ────────────

def test_run_command_echo_is_redacted(capsys):
    """R-F2796 redacted command OUTPUT but not the command LINE itself."""
    ui = _ui(False)
    logged = []
    ui._log = lambda m: logged.append(m)
    ui.tool_call("run", {"command": 'curl -H "Authorization: Bearer sk-ant-api03-ECHOSECRET1" x'})
    printed = capsys.readouterr().out
    assert "ECHOSECRET1" not in printed, f"command echoed a secret: {printed!r}"
    assert "ECHOSECRET1" not in " ".join(logged), "secret persisted to the session logfile"
    assert "curl" in printed, "the command must still be legible"


def test_non_run_tool_detail_is_redacted(capsys):
    ui = _ui(False)
    ui._log = lambda m: None
    ui.tool_call("write_file", {"path": "/x", "body": "API_KEY=sk-ant-api03-DETAILSECRET9"})
    assert "DETAILSECRET9" not in capsys.readouterr().out
