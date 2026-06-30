import type { BadgeProps } from '@/components/ui/badge';

type Variant = NonNullable<BadgeProps['variant']>;

/** First non-empty value among candidates (mirrors how the static pages read tolerant fields). */
export function pickFirst<T = unknown>(...vals: T[]): T | undefined {
  return vals.find((v) => v !== undefined && v !== null && v !== '');
}

/** Format a date that may be an ISO string or epoch (s or ms). Returns '—' if unparseable. */
export function fmtDate(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—';
  let d: Date;
  if (typeof value === 'number') {
    d = new Date(value < 1e12 ? value * 1000 : value); // <1e12 => seconds
  } else {
    d = new Date(String(value));
  }
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

/** DD risk / severity classification -> badge variant. */
export function riskVariant(risk: unknown): Variant {
  const r = String(risk || '').toUpperCase();
  // REVIEW/REQUIRED first: 'REQUIRED' contains the substring 'RED', so it must be
  // classified before the destructive RED check or it would falsely read as red.
  if (r.includes('REVIEW') || r.includes('REQUIRED')) return 'warning';
  if (r.includes('RED') || r.includes('HARD_STOP') || r.includes('HIGH') || r.includes('CRITICAL')) return 'destructive';
  if (r.includes('AMBER') || r.includes('MEDIUM') || r.includes('WARN')) return 'warning';
  if (r.includes('GREEN') || r.includes('LOW') || r.includes('CLEAR')) return 'success';
  return 'muted'; // e.g. NOT_SCREENED -> neutral
}

/** Vault entry status -> badge variant. */
export function statusVariant(status: unknown): Variant {
  const s = String(status || '').toLowerCase();
  if (s === 'verified') return 'success';
  if (s === 'needs_operator' || s === 'stale' || s === 'stale_unverified') return 'warning';
  if (s === 'declined' || s === 'error') return 'destructive';
  if (s === 'open_api') return 'default';
  return 'muted';
}

export function titleCase(s: unknown): string {
  return String(s || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}
