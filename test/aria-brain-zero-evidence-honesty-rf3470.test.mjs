// R-F3470 — zero evidence must render UNKNOWN, never a green success.

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

function elements(ids) {
  return Object.fromEntries(ids.map(id => [
    id, { textContent: '', className: '', innerHTML: '', classList: { remove() {} } },
  ]));
}

{
  const els = elements(['health-badge', 'infra-metrics', 'quality-metrics']);
  const context = {
    fetchJson: async () => ({
      status: 'degraded',
      infra: { redis: false, rag: false },
      circuit_breakers: { open: 0, registry_empty: true },
      quality: {},
    }),
    document: { getElementById: id => els[id] },
    pct: value => value == null ? '--' : `${Math.round(value * 100)}%`,
    scoreClass: () => 'neutral',
    metricRow: (label, value, cls) => `${label}:${value}:${cls}|`,
    resetBadge() {},
  };
  vm.createContext(context);
  const healthHelpers = html.slice(
    html.indexOf('function _coreCompositionRow'),
    html.indexOf('function metricRow'),
  );
  assert.match(healthHelpers, /function _coreCompositionRow/, 'health helpers must be extractable');
  vm.runInContext(`${healthHelpers}${extract('loadHealth', 'loadMode')}; this.run = loadHealth;`, context);
  await context.run();

  assert.match(els['infra-metrics'].innerHTML, /State Store Read:Unavailable:bad/);
  assert.match(els['infra-metrics'].innerHTML, /Open Breakers:UNKNOWN \(registry empty\):neutral/);
  assert.match(els['quality-metrics'].innerHTML, /UNKNOWN \(no mastery measurement\):neutral/);
  assert.doesNotMatch(els['quality-metrics'].innerHTML, /all ≥ 55%/);
}

{
  const els = elements(['halluc-metrics', 'halluc-summary', 'halluc-rate-badge']);
  const context = {
    fetchJson: async () => ({
      summary: { total_violations_24h: 0, turns_observed_24h: 0 },
      self_claim_guard: {},
      stream_guards: {},
    }),
    document: { getElementById: id => els[id] },
    escapeHtml: value => String(value),
  };
  vm.createContext(context);
  vm.runInContext(`${extract('loadHallucination', 'runStaggered')}; this.run = loadHallucination;`, context);
  await context.run();

  assert.equal(els['halluc-rate-badge'].textContent, 'NO SAMPLE (0 turns)');
  assert.equal(els['halluc-rate-badge'].className, 'badge badge-yellow');
  assert.match(els['halluc-metrics'].innerHTML, /violation rate is unmeasured/);
}

console.log('R-F3470 ARIA Brain zero-evidence honesty tests: PASS');
