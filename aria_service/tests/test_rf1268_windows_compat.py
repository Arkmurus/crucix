"""Capability test for check_windows_compat (R-F1268)."""
import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from pre_commit_checks import check_windows_compat


def test_check_windows_compat_detects_fork():
    """os.fork() should be flagged as Windows-incompatible."""
    with tempfile.TemporaryDirectory() as tmp:
        file_path = Path(tmp) / "test_module.py"
        file_path.write_text("import os\nos.fork()\n")

        issues = check_windows_compat([file_path])
        assert len(issues) >= 1
        assert "os.fork()" in issues[0]


def test_check_windows_compat_detects_fcntl():
    """fcntl usage should be flagged as Windows-incompatible."""
    with tempfile.TemporaryDirectory() as tmp:
        file_path = Path(tmp) / "test_module.py"
        file_path.write_text("import fcntl\nfcntl.flock(fd, fcntl.LOCK_EX)\n")

        issues = check_windows_compat([file_path])
        assert len(issues) >= 1
        assert "fcntl" in issues[0]


def test_check_windows_compat_detects_resource():
    """resource module usage should be flagged as Windows-incompatible."""
    with tempfile.TemporaryDirectory() as tmp:
        file_path = Path(tmp) / "test_module.py"
        file_path.write_text("import resource\nresource.setrlimit(resource.RLIMIT_NOFILE, (1024, 2048))\n")

        issues = check_windows_compat([file_path])
        assert len(issues) >= 1
        assert "resource" in issues[0]


def test_check_windows_compat_detects_pty():
    """pty module usage should be flagged as Windows-incompatible."""
    with tempfile.TemporaryDirectory() as tmp:
        file_path = Path(tmp) / "test_module.py"
        file_path.write_text("import pty\npty.spawn(['/bin/bash'])\n")

        issues = check_windows_compat([file_path])
        assert len(issues) >= 1
        assert "pty" in issues[0]


def test_check_windows_compat_passes_clean_code():
    """Clean code without Windows-incompatible patterns should pass."""
    with tempfile.TemporaryDirectory() as tmp:
        file_path = Path(tmp) / "test_module.py"
        file_path.write_text(
            "import os\n"
            "import subprocess\n"
            "result = subprocess.run(['python', '--version'], capture_output=True)\n"
        )

        issues = check_windows_compat([file_path])
        assert len(issues) == 0, f"Expected no issues, got: {issues}"
