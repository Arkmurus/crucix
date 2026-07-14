// Capability tests for the audit-batch UI fixes (static, rf391 convention).
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url'; import { dirname, join } from 'node:path';
const __d = dirname(fileURLToPath(import.meta.url));
const DASH = readFileSync(join(__d,'..','public','dashboard.html'),'utf8');
const DDR = readFileSync(join(__d,'..','public','dd-reports.html'),'utf8');
let f=0; const ck=(l,c)=>{ if(c)console.log('  ✓ '+l); else {console.error('  ✗ '+l);f++;} };
console.log('Audit-batch UI fixes\n');
// H1 (R-F2614): authed is opts-aware
ck('H1 authed(path, opts) is opts-aware (merges method/body/headers)',
  /function authed\(path,\s*opts\)/.test(DASH) && /Object\.assign\(\{\},\s*opts,\s*\{\s*headers/.test(DASH) && /opts\.body[\s\S]{0,80}Content-Type/.test(DASH));
// M2 (R-F2614): honest Active Deals on pipeline fail
ck('M2 Active-Deals renders — on pipeline-fail, not a false 0',
  /pipeUnavailable\s*=\s*true/.test(DASH) && /kpi-pipeline'\)\.textContent\s*=\s*pipeUnavailable\s*\?\s*'—'/.test(DASH));
// H2 (R-F2616): existing_case branch + Re-run anyway force:true
ck('H2 existing_case branch handles the no-run_id case',
  /started\.existing_case\s*&&\s*!runId/.test(DDR));
ck('H2 offers Re-run anyway with force:true (not false Running)',
  /dd-rerun-force/.test(DDR) && /body\.force\s*=\s*true/.test(DDR));
console.log('\n'+(f===0?'PASS':'FAIL')+' — '+f+' failure(s)');
process.exit(f===0?0:1);
