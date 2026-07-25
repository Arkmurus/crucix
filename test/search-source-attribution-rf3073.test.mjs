// R-F3073 — the Explorer must credit the source that actually answered.
//
// BROKEN PATH: lib/search/engine.mjs has had no Brave consumer since R-F373 —
// searchWeb() queries the sovereign SearXNG instance and nothing else (R-F2209).
// But the entity-search aggregator still pushed the literal 'Brave Search' into
// activeSources whenever the web bucket was non-empty. That array is not
// cosmetic: scoreConfidence() weights it, and the resulting `confidence` /
// `meetsThreshold` are what explorer.html shows the analyst. So the confidence
// on a defence-entity lookup was computed from a backend that was never called
// — a provenance claim this product cannot afford to get wrong.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const SRC = readFileSync(new URL('../lib/search/engine.mjs', import.meta.url), 'utf8');

test('R-F3082: the web label is DERIVED from the results, not hardcoded', async () => {
  // Root fix. The old line named a backend in a literal ~400 lines from the
  // fetch, so removing Brave (R-F373) could not invalidate it and the wrong
  // credit survived for two months. Deriving it means whatever answered is
  // what gets credited, and the next backend swap cannot re-open the gap.
  const { webSourceLabels, WEB_META_SOURCE } = await import('../lib/search/engine.mjs');

  assert.deepEqual(webSourceLabels([]), [],
    'an empty web bucket must contribute NO source — a dead backend must not '
    + 'inflate the confidence score');
  assert.deepEqual(webSourceLabels(undefined), [], 'a failed fetch must be treated as empty');
  assert.deepEqual(webSourceLabels([{ type: 'web', source: 'Web (google)' }]), [WEB_META_SOURCE]);
  assert.ok(!/brave/i.test(WEB_META_SOURCE),
    `the derived label still names Brave: ${WEB_META_SOURCE}`);

  const activeSourcesBlock = SRC.slice(
    SRC.indexOf('const activeSources = ['),
    SRC.indexOf('];', SRC.indexOf('const activeSources = [')),
  );
  assert.ok(!/\['Brave Search'\]|\['SearXNG/.test(activeSourcesBlock),
    'the web entry must not name a backend in a literal here — call '
    + 'webSourceLabels(webVal) so the name lives beside the fetch');
  assert.match(activeSourcesBlock, /webSourceLabels\(webVal\)/);
});

test('there is genuinely no Brave call in this module', () => {
  // Guard the premise of the test above: if Brave is ever wired in here, the
  // attribution should change back rather than this test being deleted.
  // Comments are stripped — R-F373's own note NAMES the env var it removed.
  const code = SRC.split('\n').filter(l => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n');
  assert.ok(!/process\.env\.BRAVE\w*|api\.search\.brave\.com/.test(code),
    'a Brave backend appeared in this module — revisit the activeSources label');
});

test('the new source label carries a weight (no silent drop to default)', async () => {
  const { WEB_META_SOURCE } = await import('../lib/search/engine.mjs');
  const weights = SRC.slice(SRC.indexOf('const SOURCE_WEIGHTS = {'),
                            SRC.indexOf('};', SRC.indexOf('const SOURCE_WEIGHTS = {')));
  assert.match(weights, /\[WEB_META_SOURCE\]:\s*0\.70/,
    'the weight must be keyed off the SAME constant the label comes from — an '
    + 'unweighted label silently scores at the 0.60 default, i.e. the fix would '
    + 'quietly re-score every entity lookup instead of only correcting the name');
  assert.ok(WEB_META_SOURCE.length > 0);
});

test('an exhausted SearXNG is reported, not silently returned as no results', () => {
  const webFn = SRC.slice(SRC.indexOf('async function searchWeb'),
                          SRC.indexOf('// ── News search'));
  assert.ok(!/}\s*catch\s*\{\s*\}/.test(webFn),
    'the bare `catch {}` made an unreachable/CAPTCHA-walled SearXNG look identical '
    + 'to "this entity has no web presence" — with no signal anywhere (§21a)');
  assert.match(webFn, /errorTracker\.record\(\s*'searxng'/,
    'a dead web backend must reach the brain');
});
