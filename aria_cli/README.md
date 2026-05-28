# ARIA Coder CLI (R-F988)

A local, **Claude-Code-style coding agent for ARIA**. Install it once on your
machine and run the `aria` command inside *any* project directory — ARIA reads
and edits files, runs commands and tests, uses git, and builds whatever the
task needs, driven by her own LLM brain (DeepSeek by default) instead of
Anthropic's. She works **alongside** Claude Code, with the same class of
abilities.

This is different from ARIA's *autonomous* self-coder (`aria_service/autonomous/`),
which runs on the Fly server, targets only the crucix repo, and stages changes
through an HTTP/Redis pipeline. The CLI is operator-driven, runs locally, edits
the working tree directly, and works in any directory.

## Two modes (auto-detected)

| | **self mode** | **general mode** |
|---|---|---|
| When | launched inside the crucix repo | any other directory |
| Write guard | constitutional validator **+** truncation guard | truncation guard only |
| Brain wiring | session reported to the live brain (`/api/aria/brain/signal`) when a token is set | off |

Force a mode with `--self` / `--general`.

## Setup (Windows)

```powershell
cd C:\code\crucix
.\.venv\Scripts\activate.ps1
pip install -e .          # installs the `aria` command into the venv

# LLM key (DeepSeek is ARIA's active provider):
$env:DEEPSEEK_API_KEY = "sk-..."

# Optional — wire CLI sessions back to the live brain (self-mode only):
$env:ARIA_INTERNAL_TOKEN = "<token>"
$env:ARIA_SERVICE_URL    = "https://aria-intel.fly.dev"
```

To call `aria` from any directory **without** activating the venv, copy
`aria.cmd` (or `aria.ps1`) — both in the repo root — to a folder on your `PATH`,
or add `C:\code\crucix` to `PATH`. They set `PYTHONPATH` and call the venv
Python, so `aria` works even without `pip install`.

You can always run it explicitly: `python -m aria_cli`.

## Usage

```powershell
aria                                  # interactive session in the current dir
aria "add a /health endpoint and a test for it"   # one-shot
aria -p "run the test suite and fix any failures" --auto
```

Interactive commands: `/auto` (toggle approval), `/changes`, `/reset`, `/help`,
`/exit`.

### Approval

By default ARIA **asks before** every mutating action (write / edit / run);
read-only tools run freely. Answer `y` / `n` / `a` (allow all this session), or
pass `--auto` (alias `--yolo`) to skip the prompts. Like Claude Code, she edits
the working tree directly — review with `git diff` and commit yourself.

## Tools

`read_file`, `write_file`, `edit_file`, `list_dir`, `glob`, `grep`, `run`
(shell — PowerShell on Windows, sh elsewhere). The `run` tool is how she runs
tests, git, builds, and package managers.

## Config (env)

| Var | Default | Purpose |
|---|---|---|
| `ARIA_CODER_LLM_PROVIDER` / `LLM_PROVIDER` | `deepseek` | LLM backend |
| `ARIA_CODER_LLM_API_KEY` / `LLM_API_KEY` / `DEEPSEEK_API_KEY` | — | API key |
| `ARIA_CODER_LLM_MODEL` / `LLM_MODEL` | `deepseek-chat` | model |
| `ARIA_CODER_LLM_BASE_URL` / `OPENAI_BASE_URL` | provider default | endpoint |
| `ARIA_INTERNAL_TOKEN` | — | brain auth (self-mode) |
| `ARIA_SERVICE_URL` | `https://aria-intel.fly.dev` | brain base URL |
| `ARIA_CODER_BRAIN_DISABLED` | — | set `1` to disable brain wiring |

## Safety

- **Truncation guard** (always): refuses a full-file overwrite that collapses a
  ≥40-line file below half its size — the standing protection against truncated
  stubs (mirrors R-F904).
- **Constitutional validator** (self-mode): the same deterministic guard ARIA's
  autonomous coder uses — blocks protected-file edits, dangerous imports, and
  guard/constitution removal.
- **Operator-in-the-loop**: approval prompts on by default; you commit.
