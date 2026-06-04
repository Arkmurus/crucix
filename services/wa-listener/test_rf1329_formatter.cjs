/**
 * R-F1329 — Capability test for WhatsApp Markdown formatter.
 *
 * Tests that formatForWhatsApp correctly converts Markdown to
 * WhatsApp-compatible output:
 * - Tables (|) → bullet points
 * - Headers (###) → bold with emoji
 * - Horizontal rules (---) → removed
 * - Double bold (**) → single bold (*)
 * - HTML tags → stripped
 * - Inline code (`) → triple backtick (```)
 * - Excessive blank lines → collapsed
 */

// Replicate the formatter logic from aria_wa_listener.mjs
function formatForWhatsApp(text) {
  if (!text) return text;
  let result = text;

  // 1. Strip HTML tags
  result = result.replace(/<[^>]+>/g, '');

  // 2. Convert Markdown tables
  result = result.replace(/^\|(.+)\|$/gm, (match, content) => {
    const cells = content.split('|').map(c => c.trim()).filter(c => c.length > 0);
    if (cells.every(c => /^[-:]+$/.test(c))) return '';
    if (cells.length === 2) return '\u2022 ' + cells[0] + ': ' + cells[1];
    if (cells.length >= 2) return cells.map((c, i) => i === 0 ? '\u2022 ' + c : '  ' + c).join('\n');
    return '\u2022 ' + (cells[0] || '');
  });

  // 3. Convert headers
  const HEADER_EMOJIS = {
    overview: '\ud83d\udccb', summary: '\ud83d\udccb', introduction: '\ud83d\udccb',
    findings: '\ud83d\udd0d', analysis: '\ud83d\udd0d', assessment: '\ud83d\udd0d',
    conclusion: '\u2705', result: '\u2705', outcome: '\u2705',
    risk: '\u26a0\ufe0f', risks: '\u26a0\ufe0f', warning: '\u26a0\ufe0f',
    recommendation: '\ud83d\udca1', recommendations: '\ud83d\udca1', suggestion: '\ud83d\udca1',
    'next steps': '\u27a1\ufe0f', action: '\u27a1\ufe0f', actions: '\u27a1\ufe0f',
    details: '\ud83d\udcc4', detail: '\ud83d\udcc4', information: '\ud83d\udcc4',
    background: '\u2139\ufe0f', context: '\u2139\ufe0f',
    status: '\ud83d\udcca', progress: '\ud83d\udcca',
    note: '\ud83d\udcdd', notes: '\ud83d\udcdd',
    example: '\ud83d\udd0e', examples: '\ud83d\udd0e',
  };
  result = result.replace(/^#{1,6}\s+(.+)$/gm, (match, header) => {
    const key = header.toLowerCase().trim();
    const emoji = HEADER_EMOJIS[key] || '\ud83d\udccc';
    return emoji + ' *' + header.trim() + '*';
  });

  // 4. Horizontal rules
  result = result.replace(/^[-*_]{3,}\s*$/gm, '');

  // 5. Double bold to single
  result = result.replace(/\*\*(.+?)\*\*/g, (match, p1) => '*' + p1 + '*');

  // 6. Inline code: single backtick -> triple
  result = result.replace(/(?<!\x60)\x60([^\x60]+)\x60(?!\x60)/g, (match, p1) => '\x60\x60\x60' + p1 + '\x60\x60\x60');

  // 7. Excessive blank lines
  result = result.replace(/\n{3,}/g, '\n\n');

  // 8. Trim trailing whitespace
  result = result.split('\n').map(l => l.trimEnd()).join('\n');

  return result.trim();
}

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log('  PASS:', name);
  } catch (e) {
    failed++;
    console.log('  FAIL:', name, '-', e.message);
  }
}

console.log('=== R-F1329: WhatsApp Markdown Formatter ===\n');

// Test 1: Table conversion
test('Table rows become bullet points', () => {
  const input = '| Name | Value |\n|---|---|\n| Risk | High |\n| Status | Active |';
  const out = formatForWhatsApp(input);
  if (out.includes('|')) throw new Error('Table pipes should be removed, got: ' + out);
  if (!out.includes('\u2022')) throw new Error('Should have bullet points');
});

// Test 2: Header conversion
test('Headers become bold with emoji', () => {
  const input = '### Overview\nSome text\n### Findings\nMore text';
  const out = formatForWhatsApp(input);
  if (out.includes('###')) throw new Error('Hash markers should be removed');
  if (!out.includes('*Overview*')) throw new Error('Should have bold Overview');
  if (!out.includes('*Findings*')) throw new Error('Should have bold Findings');
});

// Test 3: Horizontal rules
test('Horizontal rules are removed', () => {
  const input = 'Above\n\n---\n\nBelow';
  const out = formatForWhatsApp(input);
  if (out.includes('---')) throw new Error('HR should be removed');
});

// Test 4: Double bold
test('Double bold becomes single bold', () => {
  const input = 'This is **very** important';
  const out = formatForWhatsApp(input);
  if (out.includes('**')) throw new Error('Double bold should be single, got: ' + out);
  if (!out.includes('*very*')) throw new Error('Should be single asterisk bold, got: ' + out);
});

// Test 5: HTML stripping
test('HTML tags are stripped', () => {
  const input = 'Some <b>bold</b> and <br/> text';
  const out = formatForWhatsApp(input);
  if (out.includes('<b>') || out.includes('</b>')) throw new Error('HTML tags should be stripped');
});

// Test 6: Inline code
test('Inline code becomes triple backtick', () => {
  const input = 'Use the `scan_file` function';
  const out = formatForWhatsApp(input);
  if (!out.includes('\x60\x60\x60')) throw new Error('Should have triple backticks, got: ' + out);
});

// Test 7: Full realistic output
test('Full report renders cleanly', () => {
  const input = '## Due Diligence Report\n\n### Overview\n| Field | Value |\n|---|---|\n| Company | Acme Corp |\n| Risk Level | **High** |\n| Status | Active |\n\n### Findings\n- Sanctions match found\n- PEP status: confirmed\n\n### Recommendation\nReview required immediately.';
  const out = formatForWhatsApp(input);
  if (out.includes('|')) throw new Error('No table pipes in output');
  if (out.includes('###')) throw new Error('No hash markers in output');
  if (out.includes('**')) throw new Error('No double bold in output, got: ' + out);
  if (!out.includes('*High*')) throw new Error('Risk level should be bold');
});

// Test 8: Empty/null input
test('Empty input returns empty', () => {
  if (formatForWhatsApp('') !== '') throw new Error('Empty input should return empty');
});

// Test 9: Null/undefined input
test('Null input returns null', () => {
  if (formatForWhatsApp(null) !== null) throw new Error('Null input should return null');
});

// Test 10: Plain text unchanged
test('Plain text without Markdown is unchanged', () => {
  const input = 'Hello, this is a simple message.';
  const out = formatForWhatsApp(input);
  if (out !== input) throw new Error('Plain text should be unchanged');
});

console.log('\n=== Results: ' + passed + ' passed, ' + failed + ' failed ===');
process.exit(failed > 0 ? 1 : 0);
