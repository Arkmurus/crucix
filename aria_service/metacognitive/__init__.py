"""ARIA Metacognitive Engine — self-assessment, gap detection, calibration,
consciousness mapping, and self-improvement code generation.

This package gives ARIA three capabilities no commercial AI system has:

  1. SELF-ASSESSMENT — After every significant output, ARIA scores herself
     against professional standards across 5 dimensions.

  2. GAP DETECTION — When ARIA reads new material, she compares it against
     her capability profile and identifies what she didn't know.

  3. CONTINUOUS CALIBRATION — Brier scoring tracks whether ARIA's stated
     confidence levels match her actual accuracy over time.

  4. CONSCIOUSNESS MAPPING — Weekly self-reflection aggregates all layers
     into a living model of what ARIA knows and doesn't know.

  5. SELF-IMPROVEMENT — When persistent gaps are identified, ARIA can
     generate code proposals to close them.

All state persists to Redis (with in-memory fallback). LLM calls go
through the existing provider abstraction (never direct SDK). Feature
gated behind ARIA_METACOGNITIVE_ENABLED env var (default ON).

Architecture: designed for massive scale — every component is stateless
at the process level (Redis is the source of truth), async throughout,
cost-tracked per feature, and fail-safe (metacognitive failures never
block the chat pipeline).
"""
