// search_company.mjs
// CLI wrapper for the Crucix search engine
// Usage: node search_company.mjs "Wagner Group Angola"

import './apis/utils/env.mjs';
import { runSearch } from './lib/search/engine.mjs';

const query = process.argv.slice(2).join(' ');
if (!query) {
  console.log('Usage: node search_company.mjs "query"');
  console.log('Example: node search_company.mjs "Wagner Group Angola"');
  process.exit(1);
}

console.log(`\nSearching: ${query}\n${'='.repeat(60)}`);

const result = await runSearch(query);
const { results, totals, durationMs } = result;

const total = Object.values(totals).reduce((a, b) => a + b, 0);
console.log(`${total} results in ${durationMs}ms\n`);

const sections = [
  { key: 'intel',     label: 'LIVE INTEL' },
  { key: 'news',      label: 'NEWS' },
  { key: 'web',       label: 'WEB' },
  { key: 'social',    label: 'SOCIAL (Reddit)' },
  { key: 'companies', label: 'COMPANY REGISTRY' },
  { key: 'reference', label: 'REFERENCE' },
];

for (const { key, label } of sections) {
  const items = results[key] || [];
  if (items.length === 0) continue;
  console.log(`\n── ${label} (${items.length}) ${'─'.repeat(Math.max(0, 50 - label.length))}`);
  for (const item of items) {
    console.log(`\n  ${item.title}`);
    if (item.snippet) console.log(`  ${item.snippet.substring(0, 180)}`);
    if (item.url)     console.log(`  ${item.url}`);
    if (item.pubDate) console.log(`  ${new Date(item.pubDate).toLocaleString()}`);
  }
}

console.log(`\n${'='.repeat(60)}`);
console.log('Manual verification:');
const q = encodeURIComponent(query);
console.log(`  OFAC:          https://sanctionssearch.ofac.treas.gov/Search.aspx?searchText=${q}`);
console.log(`  OpenSanctions: https://www.opensanctions.org/search/?q=${q}`);
console.log(`  SIPRI:         https://www.sipri.org/databases/armstransfers`);
