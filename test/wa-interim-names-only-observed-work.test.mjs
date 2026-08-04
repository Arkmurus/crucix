// The WhatsApp interim may name the work ONLY when the brain reported starting it.
//
// R-F3664 removed interims like "Running the numbers — checking multiple sources"
// because this poller fires on a 7s timer and has no idea what the brain is doing.
// It stated the condition for ever being specific again: a job-kind flag, plumbed
// through, and the message gated on it.
//
// The brain now writes {stage:'tool', tool:<name>} into the job record after the
// tool is chosen and immediately before it runs, and /chat/result returns
// {job_id, **job}. `observedTool` is populated ONLY from such a poll — never
// inferred from the question — which is what makes naming the work honest.
//
// SOURCE-CONTRACT tests: aria_wa_listener.mjs opens a Baileys socket and an
// Express server at import time, so it cannot be required in a unit test. The
// repo already uses this shape (test/prospector-360-rf3651-rf3654,
// test/wa-status-admin-gate).

import { strict as assert } from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it } from 'node:test';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const WA = fs.readFileSync(path.join(ROOT, 'services/wa-listener/aria_wa_listener.mjs'), 'utf8');

describe('the interim names work only when it was observed', () => {
  it('observedTool is set ONLY from a poll reporting stage:tool', () => {
    // Capture the whole right-hand side, then drop the empty-string
    // initialiser explicitly. A negative lookahead does not work here: `\s*`
    // backtracks to zero width, so the lookahead never sees the quotes.
    const rhs = [...WA.matchAll(/observedTool\s*=\s*([^;]+);/g)].map(m => m[1].trim());
    const meaningful = rhs.filter(v => v !== "''" && v !== '""');
    assert.equal(meaningful.length, 1,
      `observedTool must have exactly one meaningful assignment; found ${meaningful.length}: ${JSON.stringify(meaningful)}`);
    assert.match(meaningful[0], /String\(st\.tool\)/,
      'it must come from the poll response, not from the question');
    assert.match(WA, /if\s*\(st\.stage === 'tool' && st\.tool\)\s*observedTool/,
      'the assignment must be gated on the brain reporting stage:tool');
  });

  it('a named interim is used only when observedTool is set', () => {
    assert.match(WA, /const _named = observedTool \? _toolInterim\[observedTool\] : null;/,
      'no observed tool must mean no specific claim');
  });

  it('falls back to the generic wording when nothing was observed', () => {
    assert.match(WA, /_named\s*\r?\n?\s*\|\|\s*_interimMessages\[/,
      'absence of a flag must never license a claim — the generic message stands');
  });

  it('the generic messages still claim only that the job is running', () => {
    const i = WA.indexOf('const _interimMessages = [');
    const block = WA.slice(i, i + 500);
    for (const banned of ['Running the numbers', 'checking multiple sources',
                          'cross-referencing', 'databases']) {
      assert.ok(!block.includes(banned),
        `R-F3664's banned fabrication "${banned}" is back in the generic interim`);
    }
  });

  it('every named interim describes work the brain actually reports', () => {
    const i = WA.indexOf('const _toolInterim = {');
    const block = WA.slice(i, WA.indexOf('};', i));
    // Keys must be tool names the brain can emit, not invented activities.
    for (const key of ['brave_answer', 'deep_research', 'dd_orchestrate', 'screen']) {
      assert.ok(block.includes(`${key}:`), `missing mapping for tool ${key}`);
    }
  });
});
