const fs = require('fs');
const f = 'server.mjs';
const lines = fs.readFileSync(f, 'utf8').split('\n');

// Find the telegram alert block and remove it
let inBlock = false;
let blockStart = -1;
let blockEnd = -1;
let depth = 0;

for (let i = 0; i < lines.length; i++) {
  const line = lines[i];
  
  // Detect start of the problematic if block around line 463-480
  if (line.includes('telegramAlerter.isConfigured') && lines[i+1] && lines[i+1].includes('filterNewSignals')) {
    blockStart = i;
    depth = 0;
    inBlock = true;
  }
  
  if (inBlock) {
    for (const ch of line) {
      if (ch === '{') depth++;
      if (ch === '}') depth--;
    }
    if (depth <= 0 && i > blockStart) {
      blockEnd = i;
      break;
    }
  }
}

if (blockStart > -1 && blockEnd > -1) {
  console.log(`Removing lines ${blockStart+1} to ${blockEnd+1}`);
  console.log('Block being removed:');
  lines.slice(blockStart, blockEnd+1).forEach((l,i) => console.log(`  ${blockStart+i+1}: ${l}`));
  
  lines.splice(blockStart, blockEnd - blockStart + 1, 
    '    // Telegram alerts handled exclusively by onSweepComplete (3-hour cadence + new intel check)');
  
  fs.writeFileSync(f, lines.join('\n'));
  console.log('Fixed successfully');
} else {
  // Fallback: just comment out line 469
  console.log('Block not found, trying line-by-line fix...');
  const content = fs.readFileSync(f, 'utf8');
  const fixed = content
    .replace(
      `        telegramAlerter.evaluateAndAlert(llmProvider, delta, memory).catch(err =>
          console.error('[Crucix] Telegram alert error:', err.message));`,
      `        // telegramAlerter.evaluateAndAlert removed — handled by onSweepComplete`
    )
    .replace(
      `        const corrMsg = formatCorrelationsForTelegram(correlations);
        if (corrMsg) {
          telegramAlerter.sendMessage(corrMsg).catch(err =>
            console.error('[Crucix] Correlation alert error:', err.message));
        }
        telegramAlerter.evaluateAndAlert(llmProvider, delta, memory).catch(err =>
          console.error('[Crucix] Telegram alert error:', err.message));`,
      `        // Direct alert calls removed — onSweepComplete handles cadence`
    );
  fs.writeFileSync(f, fixed);
  console.log('Fallback fix applied');
}
