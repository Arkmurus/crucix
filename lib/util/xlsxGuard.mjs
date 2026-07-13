// R-F2592 — DoS guard for parsing UNTRUSTED xlsx attachments.
//
// The `xlsx` (SheetJS) package has open HIGH advisories (ReDoS + prototype
// pollution) with NO upstream fix, and it is used to parse attachments that
// arrive from email (lib/aria/emailReader.mjs) and WhatsApp
// (lib/whatsapp/waListener.mjs) — i.e. attacker-controlled input. `XLSX.read`
// parses the ENTIRE buffer into memory BEFORE any row/col range cap, so an
// oversized or crafted file can hang the event loop or exhaust memory.
//
// This guard rejects oversized (and empty / non-buffer) input BEFORE the parse,
// which closes the DoS/ReDoS-by-size vector. It does NOT fix prototype
// pollution — the complete fix is migrating off `xlsx` (e.g. to exceljs), which
// is an operator-sized decision tracked separately. Cap is env-tunable; real
// business spreadsheets are well under the 15 MB default.

export const MAX_XLSX_BYTES = parseInt(
  process.env.ARIA_MAX_XLSX_BYTES || String(15 * 1024 * 1024),
  10,
);

// True iff `buffer` is a non-empty Buffer within the size cap (safe to parse).
export function xlsxSizeOk(buffer) {
  return Buffer.isBuffer(buffer) && buffer.length > 0 && buffer.length <= MAX_XLSX_BYTES;
}
