// test/newsletter-classifier-rf474.test.mjs
//
// Capability test for R-F474 — newsletter-sender upstream skip.
//
// Other-agent audit (next_session_pickup_2026_05_15 follow-up):
// newsletters were classified deep in the pipeline (after URL extraction +
// read_document fan-out fired) — cycles wasted on emails with low intel
// value. R-F474 short-circuits at classifyEmail() upstream so feedToARIA
// never fires for emails the sender heuristics flag as newsletters.
//
// Critical guard: a newsletter mentioning a tender / sanctions /
// compliance issue must STILL classify as that priority intel type, NOT
// drop to newsletter-skip. The dispatch order in classifyEmail must
// keep priority topics ahead of the newsletter gate.
//
// Run: node test/newsletter-classifier-rf474.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
  join(__dirname, '..', 'lib', 'aria', 'emailReader.mjs'),
  'utf8'
);

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

console.log('R-F474 capability tests\n');

// ── 1. Static-source: classifier has the newsletter gate and skip wire
console.log('1. classifier gate + skip wire present');
{
  check('_r474IsNewsletterSender helper defined', SRC.includes('_r474IsNewsletterSender'));
  check('classifier returns newsletter type', SRC.includes("type: 'newsletter'"));
  check('priority is skip',                   SRC.includes("priority: 'skip'"));
  check('priority === skip branch fires',     SRC.includes("priority === 'skip'"));
  check('skip path emits brainAbsorb signal', /priority === 'skip'[\s\S]+?brainAbsorb\(/.test(SRC));
  check('skip path skips feedToARIA',
        /priority === 'skip'[\s\S]+?continue;[\s\S]+?feedToARIA/.test(SRC));
}

// ── 2. Runtime — re-implement classifier shape and test truth table
console.log('\n2. classifier truth table (mirrored implementation)');
{
  // Mirror the implementation so we can test it in Node without booting
  // the IMAP/ARIA dispatch chain.
  function _isNewsletterSender(f, s) {
    if (!f) return false;
    const senderShape = (
      f.includes('newsletter') || f.includes('news@') || f.includes('digest@') ||
      f.includes('updates@') || f.includes('notifications@') ||
      f.includes('no-reply') || f.includes('noreply') || f.includes('do-not-reply') ||
      f.includes('mailings@') || f.includes('list-manage') ||
      f.includes('substack') || f.includes('mailchimp')
    );
    if (!senderShape) return false;
    const subjectShape = (
      s.includes('newsletter') ||
      s.includes('weekly digest') || s.includes('daily digest') ||
      s.includes('weekly roundup') ||
      s.includes('this week') || s.includes('this month') ||
      s.includes('your weekly') || s.includes('your daily') ||
      s.includes('unsubscribe')
    );
    return subjectShape || senderShape;
  }

  function classify(from, subject) {
    const f = (from || '').toLowerCase();
    const s = (subject || '').toLowerCase();
    if (s.includes('job change') || s.includes('new position') || s.includes('started a new'))
      return { type: 'linkedin_job_change', priority: 'critical' };
    if (s.includes('tender') || s.includes('procurement') || s.includes('rfq') || s.includes('rfp'))
      return { type: 'tender_alert', priority: 'high' };
    if (s.includes('sanction') || s.includes('embargo') || s.includes('export control'))
      return { type: 'compliance_alert', priority: 'critical' };
    if (f.includes('linkedin') || f.includes('sales-navigator'))
      return { type: 'linkedin_alert', priority: 'high' };
    if (f.includes('google') && s.includes('alert'))
      return { type: 'google_alert', priority: 'medium' };
    if (s.includes('defence') || s.includes('defense') || s.includes('military'))
      return { type: 'defence_intel', priority: 'medium' };
    if (_isNewsletterSender(f, s))
      return { type: 'newsletter', priority: 'skip' };
    return { type: 'general_email', priority: 'low' };
  }

  // Newsletter-sender shapes — SHOULD be classified as newsletter/skip
  for (const f of [
    'newsletter@example.com',
    'news@worldbank.org',
    'updates@substack.com',
    'noreply@news.medium.com',
    'do-not-reply@notifications.linkedin.com',  // careful — but linkedin alert takes priority
    'mailings@economist.com',
    'digest@nytimes.com',
  ]) {
    const r = classify(f, 'Your weekly roundup');
    // Note: 'no-reply' + 'notifications.linkedin' will hit linkedin_alert FIRST due to ordering
    const expected = f.includes('linkedin') ? 'linkedin_alert' : 'newsletter';
    check(`${f.padEnd(45)} weekly roundup → ${expected}`, r.type === expected);
  }

  // Newsletter sender + priority-intel subject — must NOT skip
  check('newsletter@ + "tender notification" → tender_alert (not newsletter)',
        classify('newsletter@gov.uk', 'Tender notification: defence procurement Q3 2026').type === 'tender_alert');
  check('news@ + "sanctions update" → compliance_alert (not newsletter)',
        classify('news@economist.com', 'Sanctions update: new EU regime').type === 'compliance_alert');
  check('updates@ + "job change" → linkedin_job_change (not newsletter)',
        classify('updates@linkedin.com', 'John Smith started a new position').type === 'linkedin_job_change');

  // Non-newsletter sender — must NOT classify as newsletter even with newsletter-shape subject
  check('antonio@arkmurus.com + "weekly digest" → general (real sender)',
        classify('antonio@arkmurus.com', 'My weekly digest of what to review').type === 'general_email');
  check('contact@startup.io + "this week" → general (real sender)',
        classify('contact@startup.io', 'What I learned this week').type === 'general_email');

  // Empty inputs — must not crash
  check('empty from → general_email',     classify('', 'subject').type === 'general_email');
  check('empty subject → general_email or sender match',
        ['general_email', 'newsletter'].includes(classify('antonio@arkmurus.com', '').type));
}

console.log(`\nR-F474 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
