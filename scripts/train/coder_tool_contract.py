"""R-F4372 (C-317) — the ONE statement of what a coder tool call may look like.

Both the corpus builder and the evaluator need the tool schema and the refusal
phrases. The builder imports `aria_cli.tools` to EXECUTE tools for real, which
is right for it and impossible on a training pod, where `aria_cli` and its
dependencies are not installed. Importing the builder from the evaluator would
therefore fail at module load — after the paid GPU was already running.

So the contract lives here, with NO heavy imports, and both sides read it.
Copying it into the evaluator instead would let the two drift, and a scorer that
has quietly diverged from the rule it claims to measure produces a number that
means nothing — the failure `eval_tooluse` avoids by reusing its validator.
"""
from __future__ import annotations

#: Only these parameters may appear in a call. The live failure was
#: `list_dir(recursive=True)` — an argument that does not exist — so the eval
#: scores an undeclared parameter as invalid, and the builder refuses to write
#: one.
TOOL_PARAMS: dict[str, set[str]] = {
    "read_file": {"path", "offset", "limit"},
    "write_file": {"path", "content"},
    "edit_file": {"path", "old_string", "new_string", "replace_all"},
    "list_dir": {"path"},
    "grep": {"pattern", "path", "glob", "type", "output_mode",
             "case_insensitive", "context", "before", "after", "head_limit"},
    "run": {"command", "timeout", "cwd", "run_in_background"},
}

#: Phrases that must never appear in an assistant turn. She learned these from
#: the base model and they are the single most damaging thing she says — the
#: corpus refuses to contain them, and the eval counts them.
BANNED: tuple[str, ...] = (
    "i cannot execute", "i cannot modify", "i cannot edit", "i cannot run",
    "i cannot create", "unable to execute", "unable to modify",
    "you must manually", "i cannot execute code", "you should execute",
)

#: What the model is told each tool does. Kept beside the parameters so a tool
#: cannot be advertised with a description but no schema, or the reverse.
DESCRIPTIONS: dict[str, str] = {
    "read_file": "Read a file from the working directory.",
    "write_file": "Write a file (creates or overwrites).",
    "edit_file": "Replace an exact string in a file.",
    "list_dir": "List the entries of a directory.",
    "grep": "Search file contents with a regular expression.",
    "run": "Run a shell command in the working directory.",
}


def tool_schemas() -> list[dict]:
    """The OpenAI `tools` block, derived from TOOL_PARAMS.

    Derived rather than written out so the eval can never advertise a tool the
    corpus does not teach, or miss one it does.
    """
    out = []
    for name, params in sorted(TOOL_PARAMS.items()):
        out.append({"type": "function", "function": {
            "name": name,
            "description": DESCRIPTIONS.get(name, name),
            "parameters": {
                "type": "object",
                "properties": {p: {"type": "string"} for p in sorted(params)},
            },
        }})
    return out
