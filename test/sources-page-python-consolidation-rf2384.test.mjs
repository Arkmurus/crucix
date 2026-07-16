import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../public/sources.html', import.meta.url), 'utf8');

function functionBody(name) {
  const start = html.indexOf(`function ${name}`);
  assert.notEqual(start, -1, `${name} must exist`);
  const brace = html.indexOf('{', start);
  let depth = 0;
  for (let i = brace; i < html.length; i += 1) {
    if (html[i] === '{') depth += 1;
    if (html[i] === '}') depth -= 1;
    if (depth === 0) return html.slice(brace + 1, i);
  }
  throw new Error(`${name} body was not closed`);
}

test('R-F2384 sources page removes stale Node/Python split', () => {
  assert.doesNotMatch(html, /Node-side sources|Python-side sources|fly brain/);
  assert.doesNotMatch(html, /\/api\/source-health/);
  assert.doesNotMatch(html, /function loadSources|loadSources\(/);

  assert.match(html, /ARIA source monitors/);
  assert.match(html, /Run source check/);
  assert.match(html, /Last Check/);
});

test('R-F2384 source summary and table share the Python uptime payload', () => {
  const body = functionBody('loadSourceMonitors');

  assert.match(body, /API\.get\('\/api\/aria\/sources\/uptime'\)/);
  assert.match(body, /updateSourceSummary\(data, sources, \{ ok: okCount, err: errCount, warn: warnCount \}\)/);
  assert.match(body, /document\.getElementById\('pysrc-body'\)/);
});

test('R-F2659 sources page surfaces stale uptime data honestly', () => {
  const body = functionBody('loadSourceMonitors');

  assert.match(body, /data\.freshness/);
  assert.match(body, /Source monitor data is stale/);
  assert.match(html, /Source check started\. Refresh this page in about a minute/);
  assert.doesNotMatch(html, /Source check completed\./);
});
