// R-F4068 (C-109) — every row under the "Auto-allowed (24h)" heading must be a
// 24h measurement, or say plainly that it is not.
//
// Live 2026-08-16 the column read:
//     Autonomous task fires   431     <- genuinely 24h (TTL'd key, verified)
//     Chat turns served       758     <- a lifetime tally; the real 24h was ~10
//     Audit-trail entries    1208     <- the LIFETIME total, unlabelled
//
// 1208 is the identical number the Chat Audit panel prints two panels down as
// "Total Entries". An operator who spots that concludes the page is broken; one
// who does not concludes ARIA produced 1,208 audited turns yesterday.
//
// The backend half is fixed in chat_audit_log (hourly buckets, R-F4068). This
// guards the half that can silently regress with a one-word edit: the label.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pageHtml } from './helpers/aria_brain_page.mjs';

/** The body of loadSurface's "Auto-allowed (24h)" column. */
function autoAllowedColumn(html) {
  const start = html.indexOf("✅ Auto-allowed (24h)");
  assert.ok(start > 0, 'aria-brain.html no longer renders the 24h column');
  // The column ends where the drafts column begins.
  const end = html.indexOf('📋 Drafts for review', start);
  assert.ok(end > start, 'could not find the end of the auto-allowed column');
  return html.slice(start, end);
}

test('R-F4068 the lifetime audit total is labelled, not shown as a day of work', () => {
  const col = autoAllowedColumn(pageHtml());
  const rows = [...col.matchAll(/metricRow\(\s*'([^']+)'\s*,\s*(auto\.[A-Za-z_]+)/g)]
    .map(m => ({ label: m[1], field: m[2] }));

  assert.ok(rows.length >= 4, `expected the 24h rows, found ${rows.length}`);

  const audit = rows.find(r => r.field === 'auto.audit_entries');
  assert.ok(audit, 'the audit-trail row is gone; if it moved, move this guard');
  assert.match(
    audit.label.toLowerCase(),
    /lifetime/,
    `"${audit.label}" reads as a 24h figure but auto.audit_entries is `
    + `chat_audit_log.get_stats().total_entries — the LIFETIME count. Either `
    + `label it lifetime or read a windowed field.`,
  );
});

test('R-F4068 chat turns served is still the windowed field', () => {
  const col = autoAllowedColumn(pageHtml());
  assert.match(
    col,
    /metricRow\('Chat turns served',\s*auto\.chat_turns_served/,
    'chat_turns_served is the 24h figure (entries_24h); do not repoint this '
    + 'row at a lifetime field to make the two numbers agree.',
  );
  assert.doesNotMatch(
    col,
    /metricRow\('Chat turns served[^']*',\s*auto\.audit_entries/,
    'chat turns must not be sourced from the lifetime total',
  );
});
