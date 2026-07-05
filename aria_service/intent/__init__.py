"""ARIA multilingual intent comprehension (Guardian Layer 3 — R-F2447).

The 'association cortex': generalises the Guardian L2 interpreter
(guardian/interpret.py) from safety commands to ARIA's FULL user-facing tool
vocabulary, in ANY language. Wired as a FALLBACK after the fast English-regex
router (routes/aria.py:_detect_tool_intent) returns None — never a replacement.
"""
