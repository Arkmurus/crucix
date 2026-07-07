import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const MVP_PAGES = [
  'public/account.html',
  'public/admin.html',
  'public/dd-reports.html',
  'public/dashboard.html',
  'public/vault.html',
  'public/sources.html',
  'public/vls-chain.html',
  'public/watchlist.html',
];

test('R-F2387 launch-critical pages use in-app Modal and Toast instead of blocking dialogs', () => {
  for (const page of MVP_PAGES) {
    const html = readFileSync(page, 'utf8');
    const withoutComments = html.replace(/<!--[\s\S]*?-->/g, '').replace(/\/\/[^\n]*/g, '');
    assert(!/(^|[^\w.])alert\s*\(/.test(withoutComments), `${page} still calls bare alert()`);
    assert(!/(^|[^\w.])confirm\s*\(/.test(withoutComments), `${page} still calls bare confirm()`);
  }
});

test('R-F2387 DD delete accepts every verified backend deletion layer', () => {
  const html = readFileSync('public/dd-reports.html', 'utf8');
  assert.match(html, /function deleteVerified\(result\)/);
  assert.match(html, /result\.blob_deleted/);
  assert.match(html, /result\.vault_deleted/);
  assert.match(html, /index_entries_removed/);
  assert.match(html, /removeDeletedReport\(runId\)/);
  assert.match(html, /removeDeletedReport\(rid\)/);
});

test('R-F2388 DD delete tombstones local rows during silent refresh', () => {
  const html = readFileSync('public/dd-reports.html', 'utf8');
  assert.match(html, /_locallyDeletedRunIds = new Set\(\)/);
  assert.match(html, /_locallyDeletedRunIds\.add\(runId\)/);
  assert.match(html, /filter\(r => !r \|\| !_locallyDeletedRunIds\.has\(r\.run_id\)\)/);
});
