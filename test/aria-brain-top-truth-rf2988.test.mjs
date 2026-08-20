// R-F2988 — capability checks for the real ARIA Brain top-bar loaders.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, '..', 'public', 'aria-brain.html'), 'utf8');

function extract(startName, endName) {
  const start = html.indexOf(`async function ${startName}`);
  const end = html.indexOf(`async function ${endName}`, start);
  assert.ok(start > 0 && end > start, `${startName} must be extractable`);
  return html.slice(start, end);
}

function baseContext(payload, ids) {
  const elements = Object.fromEntries(ids.map(id => [
    id, { textContent: '', className: '', innerHTML: '' },
  ]));
  return {
    elements,
    context: {
      fetchJson: async () => payload,
      document: { getElementById: id => elements[id] },
      pct: value => value == null ? '--' : `${Math.round(value * 100)}%`,
      scoreClass: () => 'neutral',
      metricRow: (label, value) => `${label}:${value}`,
      resetBadge: (id, label) => { elements[id].textContent = label; },
      _ageLabel: () => '',
      _ageHours: () => 0,
      _sinceBoot: () => '',
    },
  };
}

const health = baseContext(
  {
    status: 'degraded',
    infra: { redis: true, rag: true },
    circuit_breakers: { open: 0 },
    quality: {},
  },
  ['health-badge', 'infra-metrics', 'quality-metrics'],
);
vm.createContext(health.context);
const healthHelpers = html.slice(
  html.indexOf('function _coreCompositionRow'),
  html.indexOf('function metricRow'),
);
assert.match(healthHelpers, /function _coreCompositionRow/, 'health helpers must be extractable');
vm.runInContext(`${healthHelpers}${extract('loadHealth', 'loadMode')}; this.run = loadHealth;`, health.context);
await health.context.run();
assert.equal(health.elements['health-badge'].textContent, 'ECOSYSTEM: DEGRADED');

const mode = baseContext({ mode: 'NORMAL', history: [] }, ['mode-badge', 'mode-metrics']);
vm.createContext(mode.context);
vm.runInContext(`${extract('loadMode', 'loadEngine')}; this.run = loadMode;`, mode.context);
await mode.context.run();
assert.equal(mode.elements['mode-badge'].textContent, 'GUARD MODE: NORMAL');

for (const running of [true, false]) {
  const engine = baseContext(
    {
      engine: {
        enabled: true,
        running,
        autonomy_level: 3,
        autonomy_label: 'FULL',
        dry_run: false,
      },
    },
    ['autonomy-badge', 'engine-metrics'],
  );
  vm.createContext(engine.context);
  vm.runInContext(`${extract('loadEngine', 'loadAdversarial')}; this.run = loadEngine;`, engine.context);
  await engine.context.run();
  assert.equal(engine.elements['autonomy-badge'].textContent, 'AUTONOMY POLICY: FULL');
  assert.equal(
    engine.elements['autonomy-badge'].className,
    running ? 'badge badge-green' : 'badge badge-red',
  );
}

console.log('R-F2988 ARIA Brain top truth tests: PASS');
