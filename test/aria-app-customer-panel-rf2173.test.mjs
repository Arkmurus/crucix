// test/aria-app-customer-panel-rf2173.test.mjs
//
// CAPABILITY test for R-F2173 — the aria-app customer panel's data-presentation logic
// (the "data output blends with the design" requirement). Drives the REAL helpers in
// aria-app/lib/format.ts (imported via node TS type-stripping) that every P1 page uses
// to map backend fields -> shadcn badge variants + readable dates.
//
// Run: node test/aria-app-customer-panel-rf2173.test.mjs

let failures = 0;
function check(name, cond) {
  console.log(`${cond ? 'ok  ' : 'FAIL'} - ${name}`);
  if (!cond) failures++;
}

const fmt = await import('../aria-app/lib/format.ts');
const { pickFirst, fmtDate, riskVariant, statusVariant, titleCase } = fmt;

// pickFirst — tolerant field reading (reports/watchlist read severity||worst_severity||risk||...).
check('pickFirst skips null/undefined/empty', pickFirst(undefined, null, '', 'AMBER') === 'AMBER');
check('pickFirst returns first real value', pickFirst('RED', 'GREEN') === 'RED');
check('pickFirst all-empty -> undefined', pickFirst(undefined, null, '') === undefined);

// riskVariant — DD risk/severity -> badge variant (real backend values RED/AMBER/GREEN/HARD_STOP).
check('RED -> destructive', riskVariant('RED') === 'destructive');
check('HARD_STOP -> destructive', riskVariant('HARD_STOP') === 'destructive');
check('AMBER -> warning', riskVariant('AMBER') === 'warning');
check('AMBER-LIGHT -> warning', riskVariant('AMBER-LIGHT') === 'warning');
check('GREEN -> success', riskVariant('GREEN') === 'success');
check('unknown risk -> muted', riskVariant('weird') === 'muted');

// statusVariant — vault entry status (verified/needs_operator/declined/open_api).
check('verified -> success', statusVariant('verified') === 'success');
check('needs_operator -> warning', statusVariant('needs_operator') === 'warning');
check('declined -> destructive', statusVariant('declined') === 'destructive');
check('open_api -> default', statusVariant('open_api') === 'default');
check('unknown status -> muted', statusVariant('xyz') === 'muted');

// fmtDate — backend dates are ISO strings or epoch (s/ms); never crash, '—' on bad input.
check('ISO string formats', fmtDate('2026-06-30T12:00:00Z') !== '—');
check('epoch ms formats', fmtDate(1751284800000) !== '—');
check('epoch seconds formats', fmtDate(1751284800) !== '—');
check('empty -> dash', fmtDate('') === '—');
check('null -> dash', fmtDate(null) === '—');
check('garbage -> dash', fmtDate('not-a-date') === '—');

// titleCase — labels (entity_type, status) rendered human-readable.
check('snake_case -> Title Case', titleCase('needs_operator') === 'Needs Operator');
check('kebab -> Title Case', titleCase('amber-light') === 'Amber Light');

console.log(failures === 0 ? '\nALL PASS' : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
