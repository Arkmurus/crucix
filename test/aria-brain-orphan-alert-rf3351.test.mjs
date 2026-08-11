// R-F3351 — the RED completeness alert never rendered.
//
// The ARIA Ecosystem map's entire honesty design rests on one claim, stated at the
// top of aria_service/intel/ecosystem_map.py: "A module matched by NO organ is an
// ORPHAN — rendered as a RED completeness alert, so the map proves its own gaps
// instead of hiding them."
//
// It did not render. R-F2984 replaced the SVG graph with card tiles and left
// _ecoFill() — the function holding `if (n.orphan_alert) return 'var(--red)'` —
// defined and NEVER CALLED. The card renderer uses _h(n), which reads only
// n.health, and organ:unassigned can never carry a health colour: every sensor
// path resolves organs through _assign_organ, which returns null (not
// "unassigned") for an unmatched name, so no sensor ever targets that node.
//
// Net effect: "⚠ Unassigned (N)" rendered eco-h-grey — visually identical to every
// other unmeasured node — and counted toward the grey segment of the health bar.
// The gap the map exists to prove was the one thing it did not show.
//
// The server-side guard (test_rf2969_ecosystem_map.py:66) asserts the API EMITS
// orphan_alert. It passed throughout. It could not see that the consumer was gone
// — a producer/consumer defect where the carrier was deleted, so this test drives
// the REAL render path in a vm and asserts on emitted markup, never on source text.

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

/** Render the root ecosystem view against a fixture graph and return the HTML. */
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
  return { map: el.innerHTML, coverage: cv.innerHTML };
}

const COVERAGE = {
  modules: { on_map: 578, orphans: 4, pct_assigned: 99.3 },
  import_edges: { resolved_intra_repo: 2791 },
  health_sensors: { with_live_sensor: 30, total_nodes: 602, by_color: { green: 10, amber: 18, red: 2 } },
};

const GRAPH = {
  nodes: [
    { id: 'aria-intel', label: 'aria-intel (brain)', type: 'service', category: 'service', health: 'green' },
    { id: 'organ:sanctions', label: 'Sanctions & Screening', type: 'organ', category: 'sanctions', module_count: 24, health: 'green' },
    { id: 'organ:brain', label: 'Brain & Memory', type: 'organ', category: 'brain', module_count: 54 },
    { id: 'organ:unassigned', label: '⚠ Unassigned (4)', type: 'organ', category: 'unassigned', module_count: 4, orphan_alert: true },
  ],
  edges: [],
};

const { map } = await render(GRAPH, COVERAGE);

// ── the alert itself ────────────────────────────────────────────────────────
const cards = map.split('<div class="eco-card ').slice(1);
const orphanCard = cards.find(c => c.includes('Unassigned'));
assert.ok(orphanCard, 'the unassigned bucket must render a card');
assert.ok(
  orphanCard.startsWith('eco-h-red'),
  `the orphan bucket must render as a RED completeness alert, got: ${orphanCard.slice(0, 40)}`,
);

// ── a node with no sensor stays honestly grey (grey != green, and grey != red) ──
const brainCard = cards.find(c => c.includes('Brain &amp; Memory') || c.includes('Brain & Memory'));
assert.ok(brainCard, 'the unmeasured organ must still render');
assert.ok(
  brainCard.startsWith('eco-h-grey'),
  'an organ with no live sensor must stay GREY — the alert must not repaint unmeasured nodes',
);

// ── and a genuinely healthy node is unaffected ──────────────────────────────
const sanctionsCard = cards.find(c => c.includes('Sanctions'));
assert.ok(sanctionsCard.startsWith('eco-h-green'), 'a green-sensor organ must stay green');

// ── the health bar must COUNT it as red, not silently as grey ───────────────
const bar = map.slice(map.indexOf('eco-hbar'), map.indexOf('</div>', map.indexOf('eco-hbar')));
assert.ok(
  /background:#dc2626/.test(bar),
  'the health summary bar must show a red segment for the completeness alert',
);

// ── it must read as a COMPLETENESS alert, not as a broken sensor ────────────
// Red here means "these modules are unmapped", which is a different claim from
// "this organ is failing". The tooltip has to say which, or the colour lies.
assert.ok(
  /completeness/i.test(orphanCard),
  'the orphan card tooltip must say it is a completeness alert, not imply a health failure',
);

console.log('R-F3351 OK — orphan bucket renders as a RED completeness alert; grey stays grey');
