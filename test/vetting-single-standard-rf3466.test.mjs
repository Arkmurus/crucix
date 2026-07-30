import assert from 'node:assert/strict';
import fs from 'node:fs';

const html = fs.readFileSync('public/vetting.html', 'utf8');

assert.match(html, /BS 7858:2019/);
assert.doesNotMatch(html, /name: 'pack_id', label: 'Rule pack'/);
assert.match(html, /ARIA action plan/);
assert.match(html, /name: 'offer_date'/);
assert.match(html, /interview_date: values\.interview_date/);
assert.match(html, /offer_date: values\.offer_date/);
assert.match(html, /window\.setInterval/);
assert.match(html, /DETAIL_LOADING\.has\(caseId\)/);
assert.match(html, /document\.hidden/);

console.log('R-F3466 vetting single-standard page contract: PASS');
