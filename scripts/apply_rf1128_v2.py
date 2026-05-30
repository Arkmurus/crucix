"""Apply R-F1128 fix to self_coder.py — add _PROTECTED_FILES definition."""
FILEPATH = "aria_service/autonomous/self_coder.py"

with open(FILEPATH, encoding="utf-8") as f:
    content = f.read()

# Add _PROTECTED_FILES after the logger line
old = (
    'logger = logging.getLogger("aria.autonomous.self_coder")\n'
    "\n"
    "WORKSPACE_BASE"
)
new = (
    'logger = logging.getLogger("aria.autonomous.self_coder")\n'
    "\n"
    "# R-F1128 — combined set of files the autonomous loop cannot modify.\n"
    "_PROTECTED_FILES: frozenset = frozenset()\n"
    "try:\n"
    '    from .constitutional_validator import PROTECTED_FILES as _CV_PROTECTED\n'
    '    from ..intel.self_improve import NO_AUTODEPLOY_FILES as _SI_NO_AUTODEPLOY\n'
    "    _PROTECTED_FILES = frozenset(list(_CV_PROTECTED) + list(_SI_NO_AUTODEPLOY))\n"
    "except ImportError:\n"
    "    pass\n"
    "\n"
    "WORKSPACE_BASE"
)

assert old in content, "Old string not found!"
content = content.replace(old, new, 1)

with open(FILEPATH, "w", encoding="utf-8") as f:
    f.write(content)

print("OK — _PROTECTED_FILES added")
