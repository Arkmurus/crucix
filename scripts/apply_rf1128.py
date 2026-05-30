"""Apply R-F1128 fix to self_coder.py — add protected-file filter + import."""
import re

FILEPATH = "aria_service/autonomous/self_coder.py"

with open(FILEPATH, encoding="utf-8") as f:
    content = f.read()

# 1. Add PROTECTED_FILES to the constitutional_validator import
old_import = (
    "from .constitutional_validator import (\n"
    "    ConstitutionalValidator, ValidationResult,\n"
    ")"
)
new_import = (
    "from .constitutional_validator import (\n"
    "    ConstitutionalValidator, ValidationResult, PROTECTED_FILES,\n"
    ")"
)
assert old_import in content, "Import not found!"
content = content.replace(old_import, new_import, 1)
print("1. Added PROTECTED_FILES to import")

# 2. Add _PROTECTED_FILES set after the logger line
old_logger = 'logger = logging.getLogger("aria.autonomous.self_coder")\n\nWORKSPACE_BASE'
new_logger = (
    'logger = logging.getLogger("aria.autonomous.self_coder")\n'
    "\n"
    "# R-F1128 — combined set of files the autonomous loop cannot modify.\n"
    "# Merges PROTECTED_FILES (constitutional validator) with NO_AUTODEPLOY_FILES\n"
    "# (self_improve staging guard). Gaps targeting these files are filtered out\n"
    "# in _one_cycle BEFORE attempting to fix them, saving fix-slots and avoiding\n"
    "# FATAL constitutional violations.\n"
    "_PROTECTED_FILES: frozenset = frozenset()\n"
    "try:\n"
    "    from .constitutional_validator import PROTECTED_FILES as _CV_PROTECTED\n"
    "    from ..intel.self_improve import NO_AUTODEPLOY_FILES as _SI_NO_AUTODEPLOY\n"
    "    _PROTECTED_FILES = frozenset(list(_CV_PROTECTED) + list(_SI_NO_AUTODEPLOY))\n"
    "except ImportError:\n"
    "    pass\n"
    "\n"
    "WORKSPACE_BASE"
)
assert old_logger in content, "Logger line not found!"
content = content.replace(old_logger, new_logger, 1)
print("2. Added _PROTECTED_FILES set")

# 3. Replace the actionable filter in _one_cycle
old_filter = (
    "        gaps = await self.gap_detector.scan()\n"
    "        actionable = [\n"
    "            g for g in gaps\n"
    "            if g.severity >= GapSeverity.MEDIUM and g.auto_fixable\n"
    "        ]\n"
    "        if not actionable:"
)
new_filter = (
    "        gaps = await self.gap_detector.scan()\n"
    "\n"
    "        # R-F1128 — filter out gaps targeting protected files BEFORE attempting\n"
    "        # to fix them. The constitutional validator would block them anyway,\n"
    "        # but by then we have already burned a fix-slot + logged a FATAL violation.\n"
    "        # Also surface them for human review since the autonomous loop cannot fix them.\n"
    "        protected_file_gaps = []\n"
    "        actionable = []\n"
    "        for g in gaps:\n"
    "            if g.severity < GapSeverity.MEDIUM or not g.auto_fixable:\n"
    "                continue\n"
    "            # Map module name to file path and check against PROTECTED_FILES\n"
    "            _module_path = f\"aria_service/intel/{g.module}.py\"\n"
    "            if _module_path in _PROTECTED_FILES:\n"
    "                protected_file_gaps.append(g)\n"
    "                continue\n"
    "            actionable.append(g)\n"
    "\n"
    "        if protected_file_gaps:\n"
    "            logger.warning(\n"
    "                \"[aria_coder] %d gap(s) target protected files - skipped (human review needed): %s\",\n"
    "                len(protected_file_gaps),\n"
    "                [(g.gap_id, g.module, g.title[:60]) for g in protected_file_gaps],\n"
    "            )\n"
    "\n"
    "        if not actionable:"
)
assert old_filter in content, "Filter block not found!"
content = content.replace(old_filter, new_filter, 1)
print("3. Replaced actionable filter with protected-file-aware version")

with open(FILEPATH, "w", encoding="utf-8") as f:
    f.write(content)

print("Done — file updated")
