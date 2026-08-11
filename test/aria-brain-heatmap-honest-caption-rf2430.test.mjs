// test/aria-brain-heatmap-honest-caption-rf2430.test.mjs
//
// Capability test for R-F2430 — the mastery heatmap panel prepends an honest
// caption, so an operator does not read a low region cell as low competence.
// Topic mastery is a separate, strong measurement.
//
// R-F3341 — this asserted the caption's ORIGINAL claim: that a near-50% cell
// means "not been measured for that region yet", and that N/N cells are "still
// at the ~50% initial scaffold". That claim was MEASURED FALSE and deliberately
// removed:
//
//   R-F2990 replaced the score-near-0.50 proxy with the authoritative
//   samples-based count (a cell with <=1 observation). The proxy had been
//   mislabeling measured-weak cells — graded far below 0.50 by real failing
//   recalls, the very cells in the Weak-cells list — as "unmeasured scaffold",
//   which is self-contradictory.
//
//   R-F2997 then fixed the framing the proxy had justified: only ~4/189 cells
//   are actually unmeasured; the rest ARE measured and sitting low. So a low
//   cell is a MEASURED regional coverage gap, not an unmeasured one.
//
// A stale test arguing for the restoration of a claim the data disproved is
// worse than no test: it makes the honest version look like the regression.
// These now pin the CURRENT contract, including the part that matters most —
// counts come from the backend's cell_coverage when present, so reverting to
// the score proxy fails here.
//
// Drives the REAL loadHeatmap() extracted from public/aria-brain.html.
//
// Run: node test/aria-brain-heatmap-honest-caption-rf2430.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
// R-F3845 — the PAGE's own escapeHtml, for the same reason as the sibling tests.
import { escapeHtmlSource } from './helpers/aria_brain_page.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(join(__dirname, '..', 'public', 'aria-brain.html'), 'utf8');

let failures = 0;
const ok = (c, m) => { console.log(`${c ? '  ✓' : '  ✗'} ${m}`); if (!c) failures++; };

// extract the REAL loadHeatmap (closing brace at column 0)
const m = HTML.match(/async function loadHeatmap\(\)[\s\S]*?\n\}/);
ok(!!m, 'loadHeatmap() extracted from the page');
const SRC = m[0];

async function render(fixture) {
  const el = { innerHTML: '' };
  const sb = {
    Object, Set, Math, Array, console, JSON,
    document: { getElementById: (id) => (id === 'heatmap' ? el : { innerHTML: '' }) },
    fetchJson: async () => fixture,
    heatColor: () => '#fff', scoreClass: () => 'neutral',
    pct: (x) => (x == null ? '--' : Math.round(x * 100) + '%'),
  };
  vm.createContext(sb);
  // R-F3845 — the heatmap renderer now escapes its cells, and escapeHtml is
  // defined outside this slice. Run the PAGE's own copy, not a reimplementation.
  vm.runInContext(escapeHtmlSource() + '\n' + SRC + '\n;globalThis.__lh = loadHeatmap;', sb);
  await sb.__lh();
  return el.innerHTML;
}

const nine = {};
['compliance', 'procurement', 'technical', 'geopolitics', 'osint', 'relationships', 'legal', 'sanctions', 'strategic_geography']
  .forEach(t => { nine[t] = { balkans: 0.507 }; });

async function main() {
  // 1) live-shaped fixture: 1 region, 9 cells all ~0.50
  let h = await render({ heatmap: nine, weak_cells: [{ topic: 'compliance', region: 'balkans', score: 0.507 }] });
  ok(/Region-specific coverage/.test(h), 'caption present');
  ok(/measured regional coverage gap/.test(h),
     'R-F2997: a low cell is a MEASURED coverage gap, not "unmeasured"');
  ok(!/not been measured for that region yet/.test(h),
     'the disproven blanket claim must NOT come back (R-F2990 measured ~4/189 truly unmeasured)');
  ok(/not a statement about ARIA's subject competence/.test(h),
     'caption still separates coverage from competence — the reason it exists');
  ok(/Mastery panel/.test(h), 'caption points to the separate topic-mastery panel');
  ok(/1 region sampled/.test(h), 'counts regions correctly (1)');
  ok(h.indexOf('Region-specific coverage') < h.indexOf('<table'), 'caption is PREPENDED before the grid');
  ok(/balkans/.test(h) && /compliance/.test(h), 'the grid itself still renders (no regression)');

  // 2) plural region count
  const mixed = { compliance: { balkans: 0.507, east_africa: 0.82 }, osint: { balkans: 0.51 } };
  h = await render({ heatmap: mixed });
  ok(/2 regions sampled/.test(h), 'plural region count (2)');

  // 2b) R-F3341 — THE GUARD THAT MATTERS: the split comes from the backend's
  // authoritative samples-based cell_coverage, not from a score band. Scores are
  // chosen to be indistinguishable to the old proxy (all near 0.50) while the
  // backend reports something different; only a cell_coverage reader can be right.
  h = await render({
    heatmap: { compliance: { balkans: 0.507, east_africa: 0.51 }, osint: { balkans: 0.5 } },
    cell_coverage: { sampled_cells: 3, scaffold_cells: 1, measured_weak_cells: 2 },
  });
  ok(/of 3 cells, 1 not yet measured/.test(h),
     'the unmeasured count comes from cell_coverage (1), not from the score proxy (would say 3)');
  ok(/2 measured-weak/.test(h),
     'measured-weak cells are named as the real gate-#2 gaps, not relabelled unmeasured');

  // 3) empty heatmap → unchanged "No regional data yet", no caption/crash
  h = await render({ heatmap: {} });
  ok(/No regional data yet/.test(h) && !/Region-specific coverage/.test(h), 'empty heatmap path unchanged (no caption)');

  console.log(failures === 0 ? '\nPASS' : `\nFAIL (${failures})`);
  process.exit(failures === 0 ? 0 : 1);
}
main();
