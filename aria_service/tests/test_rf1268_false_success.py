"""Capability test for check_false_success (R-F1268)."""
import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from pre_commit_checks import check_false_success


def test_check_false_success_detects_unverified_success():
    """return {'success': True} without verification should be flagged."""
    with tempfile.TemporaryDirectory() as tmp:
        file_path = Path(tmp) / "test_module.py"
        file_path.write_text(
            "def do_thing():\n"
            "    return {'success': True, 'data': 'result'}\n"
        )

        issues = check_false_success([file_path])
        assert len(issues) >= 1, f"Expected false success issue, got: {issues}"
        assert "success:True" in issues[0]


def test_check_false_success_passes_verified_success():
    """return {'success': True} after verification should pass."""
    with tempfile.TemporaryDirectory() as tmp:
        file_path = Path(tmp) / "test_module.py"
        file_path.write_text(
            "def do_thing():\n"
            "    result = verify_data()\n"
            "    if result:\n"
            "        return {'success': True, 'data': result}\n"
            "    return {'success': False}\n"
        )

        issues = check_false_success([file_path])
        assert len(issues) == 0, f"Expected no issues for verified success, got: {issues}"


def test_check_false_success_passes_try_except():
    """return {'success': True} inside try/except should pass."""
    with tempfile.TemporaryDirectory() as tmp:
        file_path = Path(tmp) / "test_module.py"
        file_path.write_text(
            "def do_thing():\n"
            "    try:\n"
            "        result = perform_action()\n"
            "        return {'success': True, 'data': result}\n"
            "    except Exception as e:\n"
            "        return {'success': False, 'error': str(e)}\n"
        )

        issues = check_false_success([file_path])
        assert len(issues) == 0, f"Expected no issues for try/except, got: {issues}"


def test_check_false_success_skips_test_files():
    """Test files should not be checked for false success."""
    with tempfile.TemporaryDirectory() as tmp:
        tests_dir = Path(tmp) / "tests"
        tests_dir.mkdir(parents=True)
        file_path = tests_dir / "test_module.py"
        file_path.write_text(
            "def test_success():\n"
            "    assert {'success': True} == result\n"
        )

        issues = check_false_success([file_path])
        assert len(issues) == 0, f"Expected no issues for test file, got: {issues}"
