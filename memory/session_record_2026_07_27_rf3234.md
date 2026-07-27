# R-F3234 — Vetting header and current safety guard

- Page-level headings must survive UI simplification: removing irrelevant
  introductory copy must not remove the sole accessible `h1`.
- A capability guard must follow the current ownership model. Vetting verdicts
  are per case, so false-clean tests must drive `verdictStrip`, not a retired
  global banner whose presence would itself be misleading.
- BS 7858 coverage must remain an explicit clause register with unmodelled
  areas visible; corroborated coverage is not the same claim as certification.
