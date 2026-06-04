/**
 * R-F1321 capability tests — WA character caps + bulletproof chunking.
 *
 * Tests the splitMessage logic directly (reimplements the algorithm
 * from aria_wa_listener.mjs so we can test it without extracting it).
 * Run with: node test_rf1321_wa_character_caps.cjs
 */

const WA_MSG_LIMIT = 4000;

// Exact replica of the splitMessage function from aria_wa_listener.mjs
function splitMessage(body) {
  if (body.length <= WA_MSG_LIMIT) return [body];
  const chunks = [];
  let remaining = body;
  while (remaining.length > 0) {
    if (remaining.length <= WA_MSG_LIMIT) { chunks.push(remaining); break; }
    let cut = remaining.lastIndexOf('\n\n', WA_MSG_LIMIT);
    if (cut < WA_MSG_LIMIT * 0.3) cut = remaining.lastIndexOf('\n', WA_MSG_LIMIT);
    if (cut < WA_MSG_LIMIT * 0.3) cut = remaining.lastIndexOf('. ', WA_MSG_LIMIT);
    if (cut < WA_MSG_LIMIT * 0.3) cut = remaining.lastIndexOf(' ', WA_MSG_LIMIT);
    if (cut < WA_MSG_LIMIT * 0.3) {
      cut = remaining.lastIndexOf(' ', WA_MSG_LIMIT);
      if (cut < 10) cut = WA_MSG_LIMIT;
    }
    const chunk = remaining.slice(0, cut);
    chunks.push(chunk + (cut < WA_MSG_LIMIT ? '' : '\n[continued]'));
    remaining = remaining.slice(cut).replace(/^[\n\s]+/, '');
  }
  return chunks;
}

let passed = 0;
let failed = 0;

function assert(condition, name) {
  if (condition) {
    console.log('  PASS: ' + name);
    passed++;
  } else {
    console.error('  FAIL: ' + name);
    failed++;
  }
}

// Test 1: Short message returns as single chunk
const t1 = splitMessage('Hello world');
assert(t1.length === 1, 'Short message returns single chunk');
assert(t1[0] === 'Hello world', 'Short message content preserved');

// Test 2: Message at exactly WA_MSG_LIMIT returns as single chunk
const body2 = 'A'.repeat(WA_MSG_LIMIT);
const t2 = splitMessage(body2);
assert(t2.length === 1, 'Exact limit message returns single chunk');
assert(t2[0].length === WA_MSG_LIMIT, 'Exact limit message length preserved');

// Test 3: Message slightly over limit splits at paragraph boundary
const body3 = 'A'.repeat(100) + '\n\n' + 'B'.repeat(WA_MSG_LIMIT - 50);
const t3 = splitMessage(body3);
assert(t3.length === 2, 'Over-limit message splits into 2 chunks');
assert(t3[0].includes('[continued]'), 'First chunk has [continued] marker');
assert(!t3[1].includes('[continued]'), 'Last chunk has no [continued] marker');

// Test 4: Message never cuts mid-word
// 'word ' repeated = every token is 'word' followed by space.
// After splitting, every chunk should contain only complete 'word' tokens.
const body4 = 'word '.repeat(2000);
const t4 = splitMessage(body4);
for (let i = 0; i < t4.length; i++) {
  // Remove [continued] marker for content check
  const clean = t4[i].replace('\n[continued]', '').trim();
  // Split by space — every token should be 'word' (complete word)
  const tokens = clean.split(/\s+/);
  for (const token of tokens) {
    if (token.length > 0) {
      assert(
        token === 'word',
        'Chunk ' + i + ' contains only complete words (found: ' + JSON.stringify(token) + ')'
      );
    }
  }
}

// Test 5: Message preserves markdown structure
const body5 = '*Bold text*\n\n_Italic text_\n\n`Code block`\n\n' + 'A'.repeat(WA_MSG_LIMIT);
const t5 = splitMessage(body5);
assert(t5.length >= 2, 'Markdown message splits correctly');
assert(t5[0].includes('*Bold text*'), 'Markdown bold preserved in first chunk');
assert(t5[0].includes('_Italic text_'), 'Markdown italic preserved in first chunk');

// Test 6: Very long message (>10K) splits into multiple chunks
const body6 = 'paragraph\n\n'.repeat(3000);
const t6 = splitMessage(body6);
assert(t6.length >= 5, 'Very long message splits into 5+ chunks');
for (let i = 0; i < t6.length; i++) {
  const cleanChunk = t6[i].replace('\n[continued]', '');
  assert(
    cleanChunk.length <= WA_MSG_LIMIT,
    'Chunk ' + i + ' is under ' + WA_MSG_LIMIT + ' chars (' + cleanChunk.length + ')'
  );
}

// Test 7: Empty message returns single empty chunk
const t7 = splitMessage('');
assert(t7.length === 1, 'Empty message returns single chunk');
assert(t7[0] === '', 'Empty message content is empty string');

// Test 8: Single character returns single chunk
const t8 = splitMessage('X');
assert(t8.length === 1, 'Single char returns single chunk');
assert(t8[0] === 'X', 'Single char content preserved');

// Test 9: No chunk exceeds WA_MSG_LIMIT
const body9 = 'word '.repeat(10000);
const t9 = splitMessage(body9);
for (let i = 0; i < t9.length; i++) {
  const clean = t9[i].replace('\n[continued]', '');
  assert(clean.length <= WA_MSG_LIMIT, 'Chunk ' + i + ' within limit (' + clean.length + ' <= ' + WA_MSG_LIMIT + ')');
}

console.log('\nResults: ' + passed + ' passed, ' + failed + ' failed');
process.exit(failed > 0 ? 1 : 0);
