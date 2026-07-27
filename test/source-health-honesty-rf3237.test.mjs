import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const page = readFileSync('public/sources.html', 'utf8');
const orchestrator = readFileSync('aria_service/intel/dd_orchestrator.py', 'utf8');

test('R-F3237 Sources page does not claim an already-wired reliability path is absent', () => {
  assert.doesNotMatch(page, /has no live caller|live coverage is 0|not yet wired in production/);
  assert.match(page, /records a source observation only when a gate-cleared finding has an attributable URL/);
  assert.match(page, /sources with no observation remain unmeasured, not healthy/i);
});

test('R-F3237 measured source reliability has a real DD finalizer producer', () => {
  assert.match(orchestrator, /async def _record_source_reliability\(/);
  assert.match(orchestrator, /await web_atlas\.record_ingest\(url, layer_name, success=True\)/);
  assert.match(orchestrator, /await _record_source_reliability\(report\)/);
});
