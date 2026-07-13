// R-F2592 — xlsx DoS guard. Rejects oversized/empty/non-buffer untrusted input
// BEFORE XLSX.read (which has no upstream fix for its ReDoS/pollution advisories).
import test from 'node:test';
import assert from 'node:assert';
import { xlsxSizeOk, MAX_XLSX_BYTES } from '../lib/util/xlsxGuard.mjs';

test('accepts a normal-sized spreadsheet buffer', () => {
  assert.equal(xlsxSizeOk(Buffer.alloc(500 * 1024)), true); // 500 KB
});

test('rejects an oversized buffer (DoS vector)', () => {
  assert.equal(xlsxSizeOk(Buffer.alloc(MAX_XLSX_BYTES + 1)), false);
});

test('accepts exactly at the cap, rejects one over', () => {
  assert.equal(xlsxSizeOk(Buffer.alloc(MAX_XLSX_BYTES)), true);
  assert.equal(xlsxSizeOk(Buffer.alloc(MAX_XLSX_BYTES + 1)), false);
});

test('rejects empty and non-buffer input', () => {
  assert.equal(xlsxSizeOk(Buffer.alloc(0)), false);
  assert.equal(xlsxSizeOk(null), false);
  assert.equal(xlsxSizeOk('not a buffer'), false);
  assert.equal(xlsxSizeOk(undefined), false);
});

test('cap is a positive integer', () => {
  assert.ok(Number.isInteger(MAX_XLSX_BYTES) && MAX_XLSX_BYTES > 0);
});
