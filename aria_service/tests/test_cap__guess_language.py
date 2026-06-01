"""R-F1267 — Capability test: _guess_language and _looks_like_code helpers."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from aria_cli.cli import _guess_language, _looks_like_code


def test_guess_language_python() -> None:
    """_guess_language detects Python from def/import/class."""
    assert _guess_language("def hello():\n    print('hi')") == "python"
    assert _guess_language("import os\nimport sys") == "python"
    assert _guess_language("class Foo:\n    pass") == "python"
    assert _guess_language("from pathlib import Path") == "python"


def test_guess_language_shell() -> None:
    """_guess_language detects bash from shebang or export."""
    assert _guess_language("#!/bin/bash\necho hi") == "bash"
    assert _guess_language("export FOO=bar") == "bash"
    assert _guess_language("alias ll='ls -la'") == "bash"


def test_guess_language_json() -> None:
    """_guess_language detects JSON from { or [."""
    assert _guess_language('{"key": "value"}') == "json"
    assert _guess_language('[1, 2, 3]') == "json"


def test_guess_language_javascript() -> None:
    """_guess_language detects JS/TS from const/let/function."""
    assert _guess_language("const x = 1;") == "typescript"
    assert _guess_language("function foo() {}") == "typescript"
    assert _guess_language("import { foo } from 'bar'") == "typescript"


def test_guess_language_sql() -> None:
    """_guess_language detects SQL from SELECT/INSERT."""
    assert _guess_language("SELECT * FROM users") == "sql"
    assert _guess_language("INSERT INTO users VALUES (1)") == "sql"


def test_guess_language_fallback() -> None:
    """_guess_language falls back to 'text' for unknown content."""
    assert _guess_language("Just some random text") == "text"
    assert _guess_language("") == "text"


def test_looks_like_code_with_indentation() -> None:
    """_looks_like_code returns True for indented code."""
    code = "    def foo():\n        pass\n    return foo"
    assert _looks_like_code(code)


def test_looks_like_code_with_brackets() -> None:
    """_looks_like_code returns True for code with brackets."""
    code = "if (x > 0) {\n  return x;\n}"
    assert _looks_like_code(code)


def test_looks_like_code_short_text() -> None:
    """_looks_like_code returns False for short text."""
    assert not _looks_like_code("hi")
    assert not _looks_like_code("")


def test_looks_like_code_plain_output() -> None:
    """_looks_like_code returns False for plain output."""
    output = "Build succeeded\n0 warnings\n0 errors"
    assert not _looks_like_code(output)
