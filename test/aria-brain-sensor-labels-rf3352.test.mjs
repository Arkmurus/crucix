// R-F3352 — the header hid the actual news, and overclaimed its own scope.
//
// The live coverage banner read:
//     578 modules · 49 unassigned · 2791 imports · call-graph partial · sensors 30/602 10/18/2
//
// Two problems. First, "10/18/2" is five bare numbers with no labels and no
// tooltips: 30 of 602 nodes have ANY live sensor, and of those 30, 18 are
// degraded and 2 broken — two thirds of everything ARIA can measure about itself
// is not green, rendered as the smallest text on the card.
//
// Second, "578 modules" carried the tooltip "100% by construction: node set ==
// filesystem", which reads as the ecosystem. scan_modules() globs
// aria_service/**/*.py only and every organ is hardcoded to aria-intel, so
// aria-web and aria-wa are cards with zero modules under them — the tier holding
// auth, billing, Stripe, the UI and the WhatsApp limb. CLAUDE.md §21b is explicit
// that observability is not Python-only, so the scope is now stated, and the
// unmapped services come from the SERVER (derived from the organ table) rather
// than a hardcoded count that would quietly go stale.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
// R-F3839 — the extracted slice now escapes its output, and escapeHtml is defined
// outside every slice. Pull in the PAGE's own copy rather than reimplementing it.
import { escapeHtmlSource, elementStub } from './helpers/aria_brain_page.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, '..', 'public', 'aria-brain.html'), 'utf8');

const start = html.indexOf('let _ecoStack');
const end = html.indexOf('function ecoDrill');
assert.ok(start > 0 && end > start, 'the ecosystem renderer must be extractable');
const source = html.slice(start, end);

async function render(graph, coverage) {
  // R-F3839 — the renderer now attaches delegated click listeners (the inline
  // onclick handlers it replaced were dead under CSP script-src-attr 'none').
  const el = elementStub();
  const bc = elementStub();
  const cv = elementStub();
  const byId = { 'ecosystem-map': el, 'ecosystem-breadcrumb': bc, 'ecosystem-coverage': cv };
  const context = {
    fetchJson: async (url) => (url.startsWith('/ecosystem/coverage') ? coverage : graph),
    document: { getElementById: (id) => byId[id] },
    console,
  };
  vm.createContext(context);
  vm.runInContext(`${escapeHtmlSource()}
${source}; this.run = loadEcosystem;`, context);
  await context.run();
  return { map: el.innerHTML, banner: cv.innerHTML };
}

// The live shape, as measured on 2026-07-28.
const COVERAGE = {
  modules: { on_map: 578, orphans: 4, pct_assigned: 99.3 },
  import_edges: { resolved_intra_repo: 2791 },
  services: { declared: ['aria-intel', 'aria-wa', 'aria-web'], mapped: ['aria-intel'], unmapped: ['aria-wa', 'aria-web'] },
  health_sensors: {
    with_live_sensor: 30, total_nodes: 602, grey_no_sensor: 572,
    pct_live_sensor: 5.0, by_color: { green: 10, amber: 18, red: 2 },
  },
};
const GRAPH = {
  nodes: [
    { id: 'aria-intel', label: 'aria-intel (brain)', type: 'service', category: 'service', health: 'green' },
    { id: 'organ:brain', label: 'Brain & Memory', type: 'organ', category: 'brain', module_count: 54 },
    { id: 'organ:unassigned', label: '⚠ Unassigned (4)', type: 'organ', category: 'unassigned', module_count: 4, orphan_alert: true },
  ],
  edges: [],
};

const { map, banner } = await render(GRAPH, COVERAGE);

// ── the sensor triple must be readable without decoding ─────────────────────
assert.ok(/10\s*healthy/.test(banner), 'the green count must be labelled');
assert.ok(/18\s*degraded/.test(banner), `the degraded count must be labelled, got: ${banner}`);
assert.ok(/2\s*broken/.test(banner), 'the broken count must be labelled');
assert.ok(!/10\/18\/2/.test(banner), 'the unlabelled "10/18/2" triple must be gone');
// degraded and broken carry weight — they are the finding, not a footnote
assert.ok(/<strong>18 degraded<\/strong>/.test(banner), 'the degraded count must be emphasised');
assert.ok(/<strong>2 broken<\/strong>/.test(banner), 'the broken count must be emphasised');

// ── grey must still be explained as "not measured", never "healthy" ─────────
assert.ok(/never 'healthy'/.test(banner) || /never .healthy./.test(banner),
  'the sensor tooltip must state that grey is not-measured, not healthy');

// ── the scope must be declared, not implied ─────────────────────────────────
// The scope is DERIVED from services.mapped, so it names whatever is genuinely
// covered rather than a hardcoded label that could outlive the truth.
assert.ok(/<strong>aria-intel<\/strong>/.test(banner), 'the banner must name the scope it actually covers');
assert.ok(!/node set == filesystem"/.test(banner),
  'the old unqualified "node set == filesystem" tooltip must not survive');
assert.ok(/WITHIN aria_service\//.test(banner), 'the 100% claim must be qualified to the scanned tree');

// ── the unmapped tiers come from the SERVER, and are named ──────────────────
assert.ok(/aria-wa \+ aria-web/.test(banner), `the unmapped services must be named, got: ${banner}`);
assert.ok(/not scanned/.test(banner), 'the banner must say those tiers are not scanned');

// A payload where every service IS mapped must NOT print a warning — the line is
// derived from the server, so it disappears by itself once the gap closes.
const mapped = JSON.parse(JSON.stringify(COVERAGE));
mapped.services = { declared: ['aria-intel'], mapped: ['aria-intel'], unmapped: [] };
const { banner: clean } = await render(GRAPH, mapped);
assert.ok(!/not scanned/.test(clean), 'the unmapped warning must vanish when nothing is unmapped');

// ── the orphan card must not print its count twice ──────────────────────────
const orphanCard = map.split('<div class="eco-card ').slice(1).find(c => c.includes('Unassigned'));
const counts = (orphanCard.match(/>4</g) || []).length;
assert.equal(counts, 0, `"Unassigned (4)" must not repeat the 4 in the count column, found ${counts}`);
assert.ok(orphanCard.startsWith('eco-h-red'), 'R-F3351 must still hold: the orphan bucket stays red');

console.log('R-F3352 OK — sensor triple labelled, scope declared, unmapped tiers named by the server');
