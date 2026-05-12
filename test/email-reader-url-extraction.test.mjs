// Tests for _extractArticleUrls (lib/aria/emailReader.mjs).
//
// Hoisted out of checkInbox 2026-04-29 (F47 follow-up). The fix originally
// landed as commit c747261; verification was by smoke script. These tests
// give the QP-decode path proper coverage so a regression that re-enables
// `=3F`-residue URLs would fire CI before reaching seenode.
//
// Live failure mode reproduced below: World Bank newsletter QP-encoded
// URL that tunnelled through to /api/aria/read and 404'd in prod
// (2026-04-27 21:18:37).

import test from 'node:test';
import assert from 'node:assert/strict';
import { _extractArticleUrls } from '../lib/aria/emailReader.mjs';

test('decodes quoted-printable URLs in raw body', () => {
  const rawBody = 'Read the brief: https://t.newsletterext.worldbank.org/r/=3Fid=3D2587 and reply.';
  const urls = _extractArticleUrls({ textContent: '', rawBody });
  assert.equal(urls.length, 1);
  // Decoded form should NOT contain `=3F` or `=3D` residue
  assert.match(urls[0], /^https:\/\/t\.newsletterext\.worldbank\.org\/r\/\?id=2587/);
  assert.doesNotMatch(urls[0], /=3[FfDd]/);
});

test('drops URLs that still carry QP residue after decode', () => {
  // Soft-break QP residue that the regex didn't normalise. The defensive
  // filter must reject these so they never reach /api/aria/read.
  const rawBody = 'See https://example.com/very/long/path=\nwith-soft-break for details.';
  const urls = _extractArticleUrls({ textContent: '', rawBody });
  // The decoder strips =\n soft-breaks, so this becomes a single clean
  // URL — verify it's accepted.
  assert.equal(urls.length, 1);
  assert.equal(urls[0], 'https://example.com/very/long/pathwith-soft-break');
});

test('rejects URLs with =3F residue that survived decode', () => {
  // Construct a URL whose QP residue is NOT preceded by `=` in a way
  // the decoder would catch — simulating the edge case the residue
  // filter is there to catch.
  const textContent = 'Click https://tracker.example.com/r/x=3Fid=3D9 to read.';
  const urls = _extractArticleUrls({ textContent, rawBody: '' });
  assert.equal(urls.length, 0, 'URLs with =3F residue must be filtered out');
});

test('drops image extensions', () => {
  const rawBody = 'Logo: https://cdn.example.com/path/to/image-banner.png and the article: https://news.example.com/story-about-defence-procurement-2026';
  const urls = _extractArticleUrls({ textContent: '', rawBody });
  assert.equal(urls.length, 1);
  assert.match(urls[0], /\/story-about-defence-procurement-2026$/);
});

test('drops tracking and unsubscribe URLs', () => {
  const rawBody = `
    https://news.example.com/genuine-article-about-procurement-2026
    https://example.com/unsubscribe?token=abc
    https://click.tracking.example.com/r/12345
    https://email.foo.com/open/abc
  `;
  const urls = _extractArticleUrls({ textContent: '', rawBody });
  assert.equal(urls.length, 1);
  assert.match(urls[0], /genuine-article-about-procurement-2026$/);
});

test('caps at 5 URLs', () => {
  const lines = Array.from({ length: 12 }, (_, i) =>
    `https://news.example.com/long-defence-story-number-${i}-with-extra-padding-for-length`
  );
  const urls = _extractArticleUrls({ textContent: '', rawBody: lines.join('\n') });
  assert.equal(urls.length, 5);
});

test('drops short URLs (<30 chars)', () => {
  const rawBody = 'short: https://x.co/y and long: https://defence-news.example.com/in-depth-procurement-coverage';
  const urls = _extractArticleUrls({ textContent: '', rawBody });
  assert.equal(urls.length, 1);
  assert.match(urls[0], /defence-news\.example\.com/);
});

test('handles missing inputs without throwing', () => {
  assert.deepEqual(_extractArticleUrls({}), []);
  assert.deepEqual(_extractArticleUrls(), []);
  assert.deepEqual(_extractArticleUrls({ textContent: null, rawBody: null }), []);
});

test('combines textContent and rawBody candidates', () => {
  const textContent = 'See https://text-source.example.com/article-one-defence-procurement';
  const rawBody = 'Also https://body-source.example.com/article-two-export-controls';
  const urls = _extractArticleUrls({ textContent, rawBody });
  assert.equal(urls.length, 2);
  assert.ok(urls.some(u => u.includes('text-source')));
  assert.ok(urls.some(u => u.includes('body-source')));
});

test('F92: dedupes when same URL appears in both textContent and rawBody', () => {
  // Live newsletter pattern (2026-04-29 15:06:54): the same article link
  // appears in the plaintext part AND the HTML part. Pre-fix: the helper
  // returned the URL twice and the caller fired two /api/aria/read POSTs
  // for the same URL.
  //
  // R-F358 (2026-05-12): the original repro used a /comm/pulse/ URL which
  // is now correctly filtered as auth-required. Replaced with a public
  // pulse URL (no /comm/ prefix) that still hits the dedupe code path.
  const url = 'https://www.linkedin.com/pulse/floating-network-john-hurwitz-w8ssc-defence-2026?lipi=urn';
  const textContent = `Read the latest article: ${url}`;
  const rawBody = `Read the latest article: ${url}\n\nMore content here referencing ${url} again.`;
  const urls = _extractArticleUrls({ textContent, rawBody });
  // Despite the URL appearing 3 times across both inputs, we should
  // see it at most once in the output.
  const linkedinHits = urls.filter(u => u.includes('floating-network-john-hurwitz-w8ssc'));
  assert.equal(linkedinHits.length, 1, `Expected 1 linkedin URL after dedupe, got ${linkedinHits.length}`);
});

test('F92: dedupes within a single input source too', () => {
  // Even if all duplicates land in rawBody only, dedupe still applies.
  const url = 'https://news.example.com/long-defence-procurement-story-2026';
  const rawBody = `${url}\n\n${url}\n\n${url}\n\n${url}\n\n${url}`;
  const urls = _extractArticleUrls({ textContent: '', rawBody });
  assert.equal(urls.length, 1);
  assert.equal(urls[0], url);
});

test('R-F358: drops the exact 5 LinkedIn URLs from fly logs 2026-05-12 10:39:26-27', () => {
  // Reproduces the live log slice. Pre-fix these 5 URLs were POSTed to
  // /api/aria/read; the Python security layer blocked them (3× auth-
  // required for comm/pulse + comm/feed, 2× low-value for help/answer).
  // Post-fix none should reach the network.
  const rawBody = `
    https://www.linkedin.com/comm/pulse/machine-speed-offense-calendar-speed-defense-asymmetry-vb3ee?lipi=xx
    https://www.linkedin.com/comm/pulse/machine-speed-offense-calendar-speed-defense
    https://www.linkedin.com/help/linkedin/answer/4788?lang=en&lipi=urn%3Ali%3Apage%3Aemail_email_series
    https://www.linkedin.com/help/linkedin/answer/67?lang=en&lipi=urn%3Ali%3Apage%3Aemail_email_series_f
    https://www.linkedin.com/comm/feed/?lipi=urn%3Ali%3Apage%3Aemail_email_series_follow_newsletter_02
    https://news.example.com/genuine-defence-procurement-article-with-padding
  `;
  const urls = _extractArticleUrls({ textContent: '', rawBody });
  assert.equal(urls.length, 1, `Expected 1 URL (the genuine one), got ${urls.length}: ${JSON.stringify(urls)}`);
  assert.match(urls[0], /genuine-defence-procurement-article/);
});

test('R-F358: blocks all auth-required LinkedIn path prefixes (mirror of security.py)', () => {
  const rawBody = [
    'https://www.linkedin.com/admin/anything-padding-padding-padding',
    'https://www.linkedin.com/sales/leads-padding-padding-padding-pad',
    'https://www.linkedin.com/messaging/thread-id-padding-padding-pad',
    'https://www.linkedin.com/notifications/x-padding-padding-padding',
    'https://www.linkedin.com/in/me-padding-padding-padding-padding-x',
  ].join('\n');
  const urls = _extractArticleUrls({ textContent: '', rawBody });
  assert.equal(urls.length, 0, 'all auth-required LinkedIn paths must be filtered');
});

test('R-F358: blocks low-value LinkedIn / Twitter / Facebook help+legal paths', () => {
  const rawBody = [
    'https://www.linkedin.com/legal/user-agreement-padding-padding-pad',
    'https://www.linkedin.com/psettings/account-padding-padding-padding',
    'https://www.linkedin.com/learning/topics-padding-padding-padding-x',
    'https://help.linkedin.com/answer/12345-padding-padding-padding-pad',
    'https://twitter.com/help/article-padding-padding-padding-padding-x',
    'https://x.com/help/article-padding-padding-padding-padding-padding',
    'https://www.facebook.com/help/12345-padding-padding-padding-padding',
    'https://www.facebook.com/policies/community-padding-padding-padding',
  ].join('\n');
  const urls = _extractArticleUrls({ textContent: '', rawBody });
  assert.equal(urls.length, 0, 'all low-value navigational paths must be filtered');
});

test('R-F358: real-looking LinkedIn pulse path WITHOUT /comm/ prefix still allowed', () => {
  // Distinction matters: linkedin.com/pulse/<slug> is the public newsletter
  // article URL (no auth needed). Only the /comm/* variant 302's to login.
  // We must NOT over-filter.
  const rawBody = 'https://www.linkedin.com/pulse/genuine-public-newsletter-defence-procurement-2026';
  const urls = _extractArticleUrls({ textContent: '', rawBody });
  assert.equal(urls.length, 1, 'public /pulse/ URLs must pass through');
});
