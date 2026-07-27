# Asset licences

Provenance for third-party visual assets shipped in `public/`. **Read this before
removing an asset on licence grounds.**

## Why this file exists (R-F3315, 2026-07-27)

R-F2991 replaced the landing hero with a bespoke in-browser animation and its
commit message states the reason as: *"there is NO unlicensed/AI stock asset on a
trust-first platform."* That sentence is easy to read as a standing prohibition
on stock imagery. It is not one. The R-F2991 principle is **no asset whose
licence we cannot evidence** — the same commit deliberately preserved a drop-in
hook "if a licensed photo is set", so a licensed photo was always the intended
end state.

Without this file the next licence audit finds a stock photo, finds that commit
message, and removes a paid asset. Record the provenance here when you add one.

## Assets

| File | Source | Licence | Recorded |
|---|---|---|---|
| `public/pelican/assets/images/aria-hero-analyst.jpg` | Envato (purchased) | Envato licence, confirmed by the operator 2026-07-27 | R-F3312 / R-F3315 |

Notes on the hero:

- Derived from the purchased original
  `man-sitting-with-tablet-and-papers-in-office-2026-03-25-02-56-28-utc.jpg`
  (4587×4587). Shipped copy is cropped `top=140` to 4587×3943 and resampled to
  **1353×1163**, the exact box the previous hero occupied, because `img-fluid`
  sets `height: auto` and the rendered height therefore comes from the file's
  intrinsic aspect ratio. See `test/landing-hero-image-rf3312.test.mjs`, which
  fails if that ratio ever drifts.
- The original purchased file is **not** in the repo. Keep it somewhere the
  operator controls; regenerating the crop needs it.

## Assets that are NOT third-party

Listed so an audit does not chase them:

- `public/pelican/assets/images/aria-evidence-hero.png`,
  `aria-analyst-review.png` — project-made (R-F3297).
- `public/pelican/assets/{css,js,fonts,icons}/**` — the Pelican template and its
  bundled Bootstrap 4 / ionicons / owl-carousel / animate.css, each under its own
  upstream licence.
- The R-F2991 hero animation — bespoke, no external asset.
