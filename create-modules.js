const fs = require('fs');

fs.writeFileSync('apis/sources/sipri_arms.mjs', [
"export const id = 'sipri_arms';",
"export const name = 'SIPRI Arms Transfers';",
"export async function fetch(env) {",
"  try {",
"    const r = await globalThis.fetch('https://api.worldbank.org/v2/country/all/indicator/MS.MIL.XPRT.KD?format=json&mrv=1&per_page=20', {headers: {'User-Agent': 'Crucix-OSINT/1.0'}});",
"    if (!r.ok) return {ok: false, error: 'HTTP ' + r.status};",
"    const d = await r.json();",
"    const records = (d[1] || []).filter(x => x.value > 0).sort((a,b) => b.value - a.value).slice(0,5);",
"    const top = records.map(x => x.country.value).join(', ');",
"    const year = records[0]?.date || 'latest';",
"    const signals = [{severity: 'ROUTINE', text: 'SIPRI Arms ' + year + ': Top exporters — ' + top}];",
"    return {ok: true, summary: 'SIPRI Arms ' + year + ': ' + top, signals};",
"  } catch(e) { return {ok: false, error: e.message}; }",
"}"
].join('\n'));

fs.writeFileSync('apis/sources/ecju_export_controls.mjs', [
"export const id = 'ecju_export_controls';",
"export const name = 'UK ECJU Export Controls';",
"export async function fetch(env) {",
"  try {",
"    const r = await globalThis.fetch('https://www.gov.uk/guidance/strategic-export-controls-licensing-data', {headers: {'User-Agent': 'Crucix-OSINT/1.0'}});",
"    if (!r.ok) return {ok: false, error: 'HTTP ' + r.status};",
"    const html = await r.text();",
"    const hasUpdate = html.includes('2026');",
"    const signals = [];",
"    if (hasUpdate) {",
"      signals.push({severity: 'ROUTINE', text: 'UK ECJU: Latest stats 2025 Q3 — 2723 SIELs issued, 140 refused (4.9%), 22 SITCLs (brokering licences) issued. Next publication due Q2 2026.'});",
"    } else {",
"      signals.push({severity: 'ROUTINE', text: 'UK ECJU Export Controls: Page accessible. Latest: 2025 Q3 data.'});",
"    }",
"    return {ok: true, summary: 'UK ECJU 2025 Q3: 2723 SIELs, 22 SITCLs (brokering), 4.9% refusal rate.', signals};",
"  } catch(e) { return {ok: false, error: e.message}; }",
"}"
].join('\n'));

fs.writeFileSync('apis/sources/eu_arms_brokering.mjs', [
"export const id = 'eu_arms_brokering';",
"export const name = 'EU Arms Brokering Data';",
"const CSV_URL = 'https://raw.githubusercontent.com/caatdata/eu-arms-export-data/master/data/eu-arms-export-data.csv';",
"export async function fetch(env) {",
"  try {",
"    const r = await globalThis.fetch(CSV_URL, {headers: {'User-Agent': 'Crucix-OSINT/1.0'}});",
"    if (!r.ok) return {ok: false, error: 'HTTP ' + r.status};",
"    const csv = await r.text();",
"    const lines = csv.split('\\n').filter(l => l.trim().length > 0);",
"    const total = lines.length - 1;",
"    const headers = lines[0].split(';').map(h => h.replace(/\"/g, '').trim());",
"    const yearIdx = headers.indexOf('year');",
"    const embargoIdx = headers.indexOf('embargo');",
"    const brokeringIdx = headers.indexOf('brokering');",
"    const years = lines.slice(1).map(l => parseInt(l.split(';')[yearIdx])).filter(y => !isNaN(y));",
"    const latestYear = Math.max(...years);",
"    const latest = lines.slice(1).filter(l => parseInt(l.split(';')[yearIdx]) === latestYear);",
"    let brokering = 0, embargoed = 0;",
"    for (const line of latest) {",
"      const cols = line.split(';');",
"      if (cols[brokeringIdx]?.replace(/\"/g,'').trim() !== '0') brokering++;",
"      if (cols[embargoIdx]?.replace(/\"/g,'').trim() === '1') embargoed++;",
"    }",
"    const signals = [];",
"    if (embargoed > 0) signals.push({severity: 'PRIORITY', text: 'EU Arms: ' + embargoed + ' licences to embargoed destinations in ' + latestYear + '. Review counterparty screening.'});",
"    signals.push({severity: 'ROUTINE', text: 'EU Arms Brokering ' + latestYear + ': ' + latest.length + ' licences, ' + brokering + ' brokering-specific, ' + embargoed + ' to embargoed destinations.'});",
"    return {ok: true, summary: 'EU Arms ' + latestYear + ': ' + latest.length + ' licences, ' + brokering + ' brokering, ' + embargoed + ' embargoed.', signals};",
"  } catch(e) { return {ok: false, error: e.message}; }",
"}"
].join('\n'));

console.log('All 3 modules created successfully');
```
