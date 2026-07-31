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

function executableSource(html) {
  return html
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\/[^\n]*/g, '')
    .replace(/(['"`])(?:\\.|(?!\1)[\s\S])*\1/g, '');
}

test('R-F2387 launch-critical pages use in-app Modal and Toast instead of blocking dialogs', () => {
  for (const page of MVP_PAGES) {
    const html = readFileSync(page, 'utf8');
    const source = executableSource(html);
    assert(!/(^|[^\w.])alert\s*\(/.test(source), `${page} still calls bare alert()`);
    assert(!/(^|[^\w.])confirm\s*\(/.test(source), `${page} still calls bare confirm()`);
  }
});

test('R-F3236 blocking-dialog guard ignores prose but still catches executable calls', () => {
  assert.doesNotMatch(executableSource("Toast.show('Could not delete alert (HTTP 500)');"), /alert\s*\(/);
  assert.match(executableSource("alert ('blocking');"), /alert\s*\(/);
  assert.match(executableSource("confirm('continue?');"), /confirm\s*\(/);
});

test('R-F2387 DD delete accepts every verified backend deletion layer', () => {
  const html = readFileSync('public/dd-reports.html', 'utf8');
  assert.match(html, /function deleteVerified\(result\)/);
  assert.match(html, /result\.blob_deleted/);
  assert.match(html, /result\.vault_deleted/);
  assert.match(html, /index_entries_removed/);
  // R-F3532 — allow the delete receipt argument. The property guarded here is
  // that BOTH call sites suppress the row locally; the argument list is not the
  // contract, and pinning it went red on a change that widened the suppression
  // to the whole deleted version chain.
  assert.match(html, /removeDeletedReport\(runId\b/);
  assert.match(html, /removeDeletedReport\(rid\b/);
  // and the receipt must actually be used, or only the clicked run is suppressed
  assert.match(html, /result\.deleted_run_ids/);
});

test('R-F2388 DD delete tombstones local rows during silent refresh', () => {
  const html = readFileSync('public/dd-reports.html', 'utf8');
  assert.match(html, /_locallyDeletedRunIds = new Set\(\)/);
  assert.match(html, /_locallyDeletedRunIds\.add\(runId\)/);
  assert.match(html, /filter\(r => !r \|\| !_locallyDeletedRunIds\.has\(r\.run_id\)\)/);
});
