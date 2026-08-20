export interface OpportunitySource {
  title?: string;
  url: string;
  type?: string;
}

export interface Opportunity {
  market?: string;
  score?: number;
  tier?: string;
  complianceStatus?: string;
  type?: string;
  notes?: string;
  summary?: string;
  explorerSignals?: number;
  conflict?: { events?: number; fatalities?: number };
  procurementNeeds?: unknown[];
  sources: OpportunitySource[];
}

function text(value: unknown, maxLength: number): string | undefined {
  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim();
  return trimmed ? trimmed.slice(0, maxLength) : undefined;
}

function count(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}

function score(value: unknown): number | undefined {
  const parsed = count(value);
  return parsed !== undefined && parsed <= 100 ? parsed : undefined;
}

function isLocalHostname(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, '');
  if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local')) return true;
  if (host === '::1' || (host.includes(':') && (host.startsWith('fc') || host.startsWith('fd') || host.startsWith('fe80:')))) return true;
  const octets = host.split('.').map(Number);
  if (octets.length !== 4 || octets.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return false;
  return octets[0] === 0 || octets[0] === 10 || octets[0] === 127
    || (octets[0] === 169 && octets[1] === 254)
    || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
    || (octets[0] === 192 && octets[1] === 168);
}

/** Accept only credential-free HTTP(S) links from sweep-derived opportunity data. */
export function safeExternalUrl(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 2048) return null;
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || isLocalHostname(url.hostname)) return null;
    return url.toString();
  } catch {
    return null;
  }
}

/** Normalize and bound the untrusted `/api/opportunities` response for rendering. */
export function normalizeOpportunities(raw: unknown): Opportunity[] {
  const container = raw && typeof raw === 'object' ? raw as { opportunities?: unknown } : {};
  const candidates = Array.isArray(raw) ? raw : container.opportunities;
  if (!Array.isArray(candidates)) return [];

  return candidates.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    .slice(0, 100)
    .map((item) => {
      const conflict = item.conflict && typeof item.conflict === 'object'
        ? item.conflict as Record<string, unknown> : {};
      const sources = Array.isArray(item.sources) ? item.sources : [];
      return {
        market: text(item.market, 120),
        score: score(item.score),
        tier: text(item.tier, 40),
        complianceStatus: text(item.complianceStatus, 80),
        type: text(item.type, 80),
        notes: text(item.notes, 600),
        summary: text(item.summary, 600),
        explorerSignals: count(item.explorerSignals),
        conflict: { events: count(conflict.events), fatalities: count(conflict.fatalities) },
        procurementNeeds: Array.isArray(item.procurementNeeds) ? item.procurementNeeds.slice(0, 20) : [],
        sources: sources.flatMap((source) => {
          if (!source || typeof source !== 'object') return [];
          const record = source as Record<string, unknown>;
          const url = safeExternalUrl(record.url);
          return url ? [{ title: text(record.title, 160), url, type: text(record.type, 80) }] : [];
        }).slice(0, 4),
      };
    });
}
