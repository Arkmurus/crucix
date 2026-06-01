"""Capability test for check_wiring_present (R-F1268)."""
import sys
from pathlib import Path
import tempfile

# Add scripts to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from pre_commit_checks import check_wiring_present


def test_check_wiring_present_detects_unwired_module():
    """A module without wire_success/wire_failure should be flagged."""
    with tempfile.TemporaryDirectory() as tmp:
        intel_dir = Path(tmp) / "intel"
        intel_dir.mkdir(parents=True)
        file_path = intel_dir / "test_module.py"
        file_path.write_text("def do_something():\n    return 42\n")

        issues = check_wiring_present([file_path])
        assert len(issues) >= 1, f"Expected wiring issue, got: {issues}"
        assert "NO brain wiring" in issues[0]


def test_check_wiring_present_detects_one_sided_wiring():
    """A module with only wire_success (no wire_failure) should be flagged."""
    with tempfile.TemporaryDirectory() as tmp:
        intel_dir = Path(tmp) / "intel"
        intel_dir.mkdir(parents=True)
        file_path = intel_dir / "test_module.py"
        file_path.write_text(
            "from .engine_wiring import wire_success\n"
            "wire_success(module='test', summary='ok')\n"
        )

        issues = check_wiring_present([file_path])
        assert len(issues) >= 1, f"Expected one-sided wiring issue, got: {issues}"
        assert "wire_success but NO wire_failure" in issues[0]


def test_check_wiring_present_passes_fully_wired():
    """A module with both wire_success and wire_failure should pass."""
    with tempfile.TemporaryDirectory() as tmp:
        intel_dir = Path(tmp) / "intel"
        intel_dir.mkdir(parents=True)
        file_path = intel_dir / "test_module.py"
        file_path.write_text(
            "from .engine_wiring import wire_success, wire_failure\n"
            "wire_success(module='test', summary='ok')\n"
            "wire_failure(module='test', detail='err', gap_type='source_failure', source='test')\n"
        )

        issues = check_wiring_present([file_path])
        assert len(issues) == 0, f"Expected no issues for fully wired module, got: {issues}"


def test_check_wiring_present_skips_exempt_modules():
    """Exempt modules (__init__, main, routes, etc.) should not be flagged."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create a path that has "intel" in its parts
        intel_dir = Path(tmp) / "aria_service" / "intel"
        intel_dir.mkdir(parents=True)
        file_path = intel_dir / "__init__.py"
        file_path.write_text("# empty init\n")

        issues = check_wiring_present([file_path])
        assert len(issues) == 0, f"Expected no issues for exempt module, got: {issues}"


def test_check_wiring_present_skips_test_files():
    """Test files should not be flagged for missing wiring."""
    with tempfile.TemporaryDirectory() as tmp:
        tests_dir = Path(tmp) / "tests"
        tests_dir.mkdir(parents=True)
        file_path = tests_dir / "test_something.py"
        file_path.write_text("def test_something():\n    pass\n")

        issues = check_wiring_present([file_path])
        assert len(issues) == 0, f"Expected no issues for test file, got: {issues}"
