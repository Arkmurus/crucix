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
  // R-F2384 removed a CONFUSING dual view of the SAME sources (an old loadSources() that
  // fetched /api/source-health competing with the Python uptime view). Those guards STAY.
  assert.doesNotMatch(html, /Node-side sources|Python-side sources|fly brain/);
  assert.doesNotMatch(html, /function loadSources|loadSources\(/);
  // R-F2721 (Codex source-health audit #1) SUPERSEDES the old `/api/source-health` ban:
  // that endpoint is the OPERATIONAL briefing-feed tracker (50 feeds) — a DIFFERENT source
  // set from the catalogue-reachability uptime monitor (200 URLs). Removing it entirely was
  // the Codex #1 gap ("the page omits the operational feed-health system"). It is now
  // re-added as a DISTINCT, clearly-labelled `loadOperationalFeeds` panel (not the old
  // split), so it must be PRESENT, not absent.
  assert.match(html, /\/api\/source-health/);
  assert.match(html, /loadOperationalFeeds/);

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
