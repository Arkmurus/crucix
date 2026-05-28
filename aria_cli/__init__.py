"""R-F988 — ARIA Coder CLI.

A local, Claude-Code-style coding agent for ARIA. Install once, run the
``aria`` command inside *any* project directory, and ARIA reads/edits files,
runs commands, and builds whatever the task requires — driven by her own LLM
brain (DeepSeek by default) instead of Anthropic's.

Two operating modes, auto-detected:

  * **self mode**  — launched inside the crucix repo (ARIA's own ecosystem).
    Full constitutional validation + truncation guard on writes, and every
    session reports back to the live brain (``/api/aria/brain/signal``) so the
    work is wired-to-brain (CLAUDE.md §21) and pay-once-remember-forever (§15).

  * **general mode** — launched in any other directory. ARIA behaves like a
    general coding agent (the truncation safety-net still applies); no
    crucix-specific constitution and no brain wiring unless explicitly
    configured.

This package is deliberately light: it imports the heavy ``aria_service``
modules lazily and degrades gracefully when they are unavailable, so the CLI
runs standalone on the operator's machine without booting the FastAPI brain.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
