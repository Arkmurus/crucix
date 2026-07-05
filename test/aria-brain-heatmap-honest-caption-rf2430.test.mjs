// test/aria-brain-heatmap-honest-caption-rf2430.test.mjs
//
// Capability test for R-F2430 — the mastery heatmap panel prepends an honest
// caption so a near-50% region cell reads as "not measured for that region
// yet" (gate #2 region-signal starvation), NOT as low competence. Topic
// mastery is a separate, strong measurement.
//
// Drives the REAL loadHeatmap() extracted from public/aria-brain.html.
//
// Run: node test/aria-brain-heatmap-honest-caption-rf2430.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

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
  vm.runInContext(SRC + '\n;globalThis.__lh = loadHeatmap;', sb);
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
  ok(/not been measured for that region yet/.test(h), 'caption explains near-50% = unmeasured, not incompetence');
  ok(/Mastery panel/.test(h), 'caption points to the separate topic-mastery panel');
  ok(/1 region has regional samples so far/.test(h), 'counts regions correctly (1)');
  ok(/9\/9 cells still at the ~50% initial scaffold/.test(h), 'counts near-initial cells (9/9)');
  ok(h.indexOf('Region-specific coverage') < h.indexOf('<table'), 'caption is PREPENDED before the grid');
  ok(/balkans/.test(h) && /compliance/.test(h), 'the grid itself still renders (no regression)');

  // 2) mixed: 2 regions, one measured cell (0.82) + two near-initial
  const mixed = { compliance: { balkans: 0.507, east_africa: 0.82 }, osint: { balkans: 0.51 } };
  h = await render({ heatmap: mixed });
  ok(/2 regions have regional samples so far/.test(h), 'plural region count (2)');
  ok(/2\/3 cells still at the ~50% initial scaffold/.test(h), 'near-initial excludes the measured 0.82 cell (2/3)');

  // 3) empty heatmap → unchanged "No regional data yet", no caption/crash
  h = await render({ heatmap: {} });
  ok(/No regional data yet/.test(h) && !/Region-specific coverage/.test(h), 'empty heatmap path unchanged (no caption)');

  console.log(failures === 0 ? '\nPASS' : `\nFAIL (${failures})`);
  process.exit(failures === 0 ? 0 : 1);
}
main();
