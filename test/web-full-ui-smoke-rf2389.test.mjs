import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it } from 'node:test';

const ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
  '..',
);
const PUBLIC = path.join(ROOT, 'public');

function read(rel) {
  return fs.readFileSync(path.join(ROOT, rel), 'utf8');
}

function publicPath(target) {
  const clean = target.split('#')[0].split('?')[0];
  if (!clean || clean.startsWith('#')) return null;
  if (/^(https?:|mailto:|tel:|javascript:|data:|blob:|ws:|wss:)/i.test(clean)) return null;
  if (clean.startsWith('/api/')) return null;
  if (clean.startsWith('/socket.io/')) return null;
  if (clean.startsWith('//')) return null;
  return clean.startsWith('/') ? path.join(PUBLIC, clean.slice(1)) : path.join(PUBLIC, clean);
}

function htmlFiles() {
  return fs.readdirSync(PUBLIC)
    .filter((name) => name.endsWith('.html'))
    .sort();
}

function assertHasAll(source, page, snippets) {
  for (const snippet of snippets) {
    assert.ok(source.includes(snippet), `${page} must include ${snippet}`);
  }
}

describe('R-F2389 full public web smoke guard', () => {
  it('all first-party page, script, style, font, and image references resolve locally', () => {
    const missing = [];
    const attrRe = /\b(?:href|src)=["']([^"']+)["']/g;
    const checkedExt = /\.(?:html|js|css|png|jpg|jpeg|webp|svg|ico|woff2?|ttf)$/i;

    for (const page of htmlFiles()) {
      const html = fs.readFileSync(path.join(PUBLIC, page), 'utf8');
      for (const match of html.matchAll(attrRe)) {
        const clean = match[1].split('#')[0].split('?')[0];
        if (!checkedExt.test(clean)) continue;
        const filePath = publicPath(match[1]);
        if (filePath && !fs.existsSync(filePath)) {
          missing.push(`${page} -> ${match[1]}`);
        }
      }
    }

    assert.deepEqual(missing, [], 'missing first-party web assets or local pages');
  });

  it('no public page links users to the retired login.html entrypoint', () => {
    const offenders = htmlFiles().filter((page) => read(`public/${page}`).includes('login.html'));
    assert.deepEqual(offenders, [], 'public pages must link to signin.html, not login.html');
  });

  it('vault primary actions are wired to the Python vault API and refresh the table', () => {
    const html = read('public/vault.html');
    assertHasAll(html, 'vault.html', [
      "document.getElementById('btn-add-site').addEventListener('click', open)",
      "document.getElementById('as-submit').addEventListener('click', async () =>",
      "API.post('/api/aria/vault', body)",
      "document.getElementById('btn-clear-vault')",
      "authed('/api/aria/vault', { method: 'DELETE' })",
      'loadVault();',
    ]);
  });

  it('sources Add intel source writes to vault-curated News Monitor output only', () => {
    const html = read('public/sources.html');
    assertHasAll(html, 'sources.html', [
      "document.getElementById('btn-run-pysrc').addEventListener('click'",
      "fetch('/api/aria/sources/uptime/run'",
      "document.getElementById('btn-run-adv').addEventListener('click'",
      "fetch('/api/aria/adversarial/run_weekly'",
      "var addBtn = document.getElementById('btn-add-intel-source')",
      "API.post('/api/aria/vault', body)",
      "site_type: type, status: 'verified'",
    ]);
    assert.ok(
      !/API\.post\('\/api\/aria\/sources\/uptime'/.test(html),
      'sources.html must not mutate built-in ARIA source monitors',
    );
  });

  it('DD report delete removes rows immediately and protects against stale refresh re-render', () => {
    const html = read('public/dd-reports.html');
    assertHasAll(html, 'dd-reports.html', [
      'const _locallyDeletedRunIds = new Set();',
      '_locallyDeletedRunIds.add(runId);',
      'filter(r => !r || !_locallyDeletedRunIds.has(r.run_id))',
      'removeDeletedReport(runId);',
      'removeDeletedReport(rid);',
    ]);
  });

  it('dashboard Golden Intel and source controls are wired to live data endpoints', () => {
    const html = read('public/dashboard.html');
    assertHasAll(html, 'dashboard.html', [
      'id="golden-intel-card"',
      '/api/aria/intel/signals/recent?limit=20',
      "document.getElementById('btn-add-source').addEventListener('click'",
      "document.getElementById('src-submit').addEventListener('click'",
      "/api/aria/user/sources",
    ]);
  });
});
