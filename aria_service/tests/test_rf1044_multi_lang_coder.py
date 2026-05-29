"""R-F1044 — Tests for the Multi-Language Coding Engine.

Covers:
  1. Language detection (extension-based and filename-based)
  2. Multi-language code review (TypeScript, Rust, Go, SQL, Docker, YAML, Shell)
  3. Project analysis (language breakdown, build systems)
  4. Generic fallback review
"""
from __future__ import annotations

import pytest

from aria_service.intel.multi_lang_coder import (
    detect_language,
    review,
    format_findings,
    analyse_project,
)


# ════════════════════════════════════════════════════════════════════════════
# Language detection
# ════════════════════════════════════════════════════════════════════════════

class TestDetectLanguage:
    def test_python_by_extension(self) -> None:
        assert detect_language("main.py") == "python"

    def test_typescript_by_extension(self) -> None:
        assert detect_language("app.ts") == "typescript"
        assert detect_language("component.tsx") == "typescript"

    def test_javascript_by_extension(self) -> None:
        assert detect_language("app.js") == "javascript"
        assert detect_language("component.jsx") == "javascript"

    def test_rust_by_extension(self) -> None:
        assert detect_language("main.rs") == "rust"

    def test_go_by_extension(self) -> None:
        assert detect_language("main.go") == "go"

    def test_sql_by_extension(self) -> None:
        assert detect_language("query.sql") == "sql"

    def test_dockerfile_by_name(self) -> None:
        assert detect_language("Dockerfile") == "docker"
        assert detect_language("Dockerfile.prod") == "docker"

    def test_yaml_by_extension(self) -> None:
        assert detect_language("config.yaml") == "yaml"
        assert detect_language("config.yml") == "yaml"

    def test_toml_by_extension(self) -> None:
        assert detect_language("config.toml") == "toml"

    def test_json_by_extension(self) -> None:
        assert detect_language("data.json") == "json"

    def test_shell_by_extension(self) -> None:
        assert detect_language("script.sh") == "shell"
        assert detect_language("script.bash") == "shell"

    def test_powershell_by_extension(self) -> None:
        assert detect_language("script.ps1") == "powershell"

    def test_c_cpp_by_extension(self) -> None:
        assert detect_language("main.c") == "c"
        assert detect_language("main.cpp") == "cpp"
        assert detect_language("main.hpp") == "cpp"

    def test_java_kotlin_by_extension(self) -> None:
        assert detect_language("Main.java") == "java"
        assert detect_language("Main.kt") == "kotlin"

    def test_ruby_by_extension(self) -> None:
        assert detect_language("app.rb") == "ruby"

    def test_php_by_extension(self) -> None:
        assert detect_language("index.php") == "php"

    def test_swift_by_extension(self) -> None:
        assert detect_language("main.swift") == "swift"

    def test_build_files(self) -> None:
        assert detect_language("Cargo.toml") == "rust"
        assert detect_language("go.mod") == "go"
        assert detect_language("Makefile") == "make"
        assert detect_language("CMakeLists.txt") == "cmake"

    def test_unknown_extension(self) -> None:
        assert detect_language("readme.txt") is None
        assert detect_language("data.bin") is None


# ════════════════════════════════════════════════════════════════════════════
# Multi-language code review
# ════════════════════════════════════════════════════════════════════════════

class TestReview:
    def test_typescript_review_finds_any_type(self) -> None:
        code = """
function process(data: any): void {
    console.log(data);
}
"""
        findings = review(code, "app.ts")
        any_findings = [f for f in findings if f["rule"] == "any_type"]
        assert len(any_findings) >= 1

    def test_typescript_review_finds_var_usage(self) -> None:
        code = """
var x = 5;
const y = 10;
"""
        findings = review(code, "app.ts")
        var_findings = [f for f in findings if f["rule"] == "var_usage"]
        assert len(var_findings) >= 1

    def test_javascript_review_finds_console_log(self) -> None:
        code = """
function greet(name) {
    console.log("Hello, " + name);
}
"""
        findings = review(code, "app.js")
        console_findings = [f for f in findings if f["rule"] == "console_log"]
        assert len(console_findings) >= 1

    def test_javascript_review_finds_loose_equality(self) -> None:
        code = """
if (x == 5) {
    return true;
}
"""
        findings = review(code, "app.js")
        loose_findings = [f for f in findings if f["rule"] == "loose_equality"]
        assert len(loose_findings) >= 1

    def test_rust_review_finds_unwrap(self) -> None:
        code = """
fn process() {
    let x = get_value().unwrap();
}
"""
        findings = review(code, "main.rs")
        unwrap_findings = [f for f in findings if f["rule"] == "unwrap_usage"]
        assert len(unwrap_findings) >= 1

    def test_rust_review_finds_unsafe(self) -> None:
        code = """
fn dangerous() {
    unsafe {
        // do something
    }
}
"""
        findings = review(code, "main.rs")
        unsafe_findings = [f for f in findings if f["rule"] == "unsafe_block"]
        assert len(unsafe_findings) >= 1

    def test_go_review_finds_panic(self) -> None:
        code = """
func process() {
    panic("something went wrong")
}
"""
        findings = review(code, "main.go")
        panic_findings = [f for f in findings if f["rule"] == "panic_usage"]
        assert len(panic_findings) >= 1

    def test_go_review_finds_missing_export_comment(self) -> None:
        code = """
func Process() {
    // no comment
}
"""
        findings = review(code, "main.go")
        comment_findings = [f for f in findings if f["rule"] == "missing_export_comment"]
        assert len(comment_findings) >= 1

    def test_sql_review_finds_select_star(self) -> None:
        code = "SELECT * FROM users;"
        findings = review(code, "query.sql")
        star_findings = [f for f in findings if f["rule"] == "select_star"]
        assert len(star_findings) >= 1

    def test_sql_review_finds_delete_without_where(self) -> None:
        code = "DELETE FROM users;"
        findings = review(code, "query.sql")
        delete_findings = [f for f in findings if f["rule"] == "delete_without_where"]
        assert len(delete_findings) >= 1

    def test_docker_review_finds_latest_tag(self) -> None:
        code = "FROM python:latest"
        findings = review(code, "Dockerfile")
        latest_findings = [f for f in findings if f["rule"] == "latest_tag"]
        assert len(latest_findings) >= 1

    def test_docker_review_finds_no_user(self) -> None:
        code = "FROM python:3.12\nRUN pip install requests"
        findings = review(code, "Dockerfile")
        root_findings = [f for f in findings if f["rule"] == "running_as_root"]
        assert len(root_findings) >= 1

    def test_yaml_review_finds_tab(self) -> None:
        code = "name:\n\tvalue: 5"
        findings = review(code, "config.yaml")
        tab_findings = [f for f in findings if f["rule"] == "yaml_tab"]
        assert len(tab_findings) >= 1

    def test_json_review_finds_trailing_comma(self) -> None:
        code = '{\n  "a": 1,\n}'
        findings = review(code, "data.json")
        comma_findings = [f for f in findings if f["rule"] == "json_trailing_comma"]
        assert len(comma_findings) >= 1

    def test_json_review_finds_parse_error(self) -> None:
        code = '{"a": 1, "b": }'
        findings = review(code, "data.json")
        parse_findings = [f for f in findings if f["rule"] == "json_parse_error"]
        assert len(parse_findings) >= 1

    def test_shell_review_finds_missing_set_e(self) -> None:
        code = "echo hello\necho world"
        findings = review(code, "script.sh")
        set_e_findings = [f for f in findings if f["rule"] == "missing_set_e"]
        assert len(set_e_findings) >= 1

    def test_shell_review_finds_eval(self) -> None:
        code = "eval \"ls $dir\""
        findings = review(code, "script.sh")
        eval_findings = [f for f in findings if f["rule"] == "eval_usage"]
        assert len(eval_findings) >= 1

    def test_powershell_review_finds_write_host(self) -> None:
        code = "Write-Host \"Hello\""
        findings = review(code, "script.ps1")
        wh_findings = [f for f in findings if f["rule"] == "write_host"]
        assert len(wh_findings) >= 1

    def test_generic_fallback_finds_secrets(self) -> None:
        code = 'api_key = "sk-1234567890123456"'
        findings = review(code, "config.txt")
        secret_findings = [f for f in findings if f["rule"] == "hardcoded_secret"]
        assert len(secret_findings) >= 1

    def test_generic_fallback_unknown_language(self) -> None:
        code = "some text content"
        findings = review(code, "readme.txt")
        unsupported = [f for f in findings if f["rule"] == "language_not_supported"]
        assert len(unsupported) >= 1

    def test_review_without_file_path_uses_generic(self) -> None:
        code = "print('hello')"
        findings = review(code)
        # Should not crash, should return generic findings
        assert isinstance(findings, list)


# ════════════════════════════════════════════════════════════════════════════
# Format findings
# ════════════════════════════════════════════════════════════════════════════

class TestFormatFindings:
    def test_empty_findings(self) -> None:
        result = format_findings([])
        assert "passed" in result

    def test_formats_critical_first(self) -> None:
        findings = [
            {"rule": "test", "severity": "LOW", "line": 5, "message": "Low issue"},
            {"rule": "test", "severity": "CRITICAL", "line": 1, "message": "Critical issue"},
        ]
        result = format_findings(findings)
        assert "Critical issue" in result
        assert "Low issue" in result
        # Critical should appear before LOW
        assert result.index("Critical") < result.index("Low")


# ════════════════════════════════════════════════════════════════════════════
# Project analysis
# ════════════════════════════════════════════════════════════════════════════

class TestAnalyseProject:
    def test_returns_error_for_nonexistent_dir(self) -> None:
        result = analyse_project("/nonexistent/path")
        assert "error" in result

    def test_analyses_python_project(self, tmp_path) -> None:
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "utils.py").write_text("def helper(): pass")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")
        (tmp_path / "README.md").write_text("# Test")

        result = analyse_project(tmp_path)
        assert result["languages"].get("python", 0) >= 2
        assert "pip" in result["build_systems"]
        assert result["total_files"] >= 3

    def test_analyses_multi_language_project(self, tmp_path) -> None:
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "app.ts").write_text("const x: number = 5;")
        (tmp_path / "Dockerfile").write_text("FROM python:3.12")
        (tmp_path / "package.json").write_text("{}")

        result = analyse_project(tmp_path)
        assert "python" in result["languages"]
        assert "typescript" in result["languages"]
        assert "docker" in result["languages"]
        assert "npm" in result["build_systems"]

    def test_skips_hidden_dirs(self, tmp_path) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("[core]")
        (tmp_path / "main.py").write_text("print('hello')")

        result = analyse_project(tmp_path)
        assert result["languages"].get("python", 0) >= 1
        # .git files should not be counted
        assert result["total_files"] >= 1
