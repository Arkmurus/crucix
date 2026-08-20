import { safeExternalUrl } from './opportunities.ts';

export type SourceMutationState =
  | { status: 'idle'; message: '' }
  | { status: 'success'; message: string }
  | { status: 'error'; message: string };

export interface UserSource {
  siteId: string;
  name: string;
  url: string;
  siteType?: string;
  status?: string;
  createdAt?: string | number;
  updatedAt?: string | number;
  lastVerifiedAt?: string | number;
}

type RequestFn = (path: string, init?: RequestInit) => Promise<unknown>;

function text(value: unknown, maxLength: number): string | undefined {
  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim();
  return trimmed ? trimmed.slice(0, maxLength) : undefined;
}

/** Normalize the JWT-scoped source response and drop records unsafe to render. */
export function normalizeUserSources(raw: unknown): UserSource[] {
  const container = raw && typeof raw === 'object' ? raw as { sources?: unknown } : {};
  if (!Array.isArray(container.sources)) return [];
  return container.sources.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const source = item as Record<string, unknown>;
    const siteId = text(source.site_id, 200);
    const url = safeExternalUrl(source.site_url);
    if (!siteId || !url) return [];
    return [{
      siteId,
      name: text(source.site_name, 160) || siteId,
      url,
      siteType: text(source.site_type, 40),
      status: text(source.status, 80),
      createdAt: typeof source.created_at === 'string' || typeof source.created_at === 'number' ? source.created_at : undefined,
      updatedAt: typeof source.updated_at === 'string' || typeof source.updated_at === 'number' ? source.updated_at : undefined,
      lastVerifiedAt: typeof source.last_verified_at === 'string' || typeof source.last_verified_at === 'number' ? source.last_verified_at : undefined,
    }];
  }).slice(0, 25);
}

/** Perform one tenant-scoped source mutation and require semantic confirmation. */
export async function performSourceMutation(
  request: RequestFn,
  operation: 'add' | 'remove',
  values: { name?: string; url?: string; siteType?: string; notes?: string; siteId?: string },
): Promise<SourceMutationState> {
  const name = String(values.name || '').trim();
  const url = String(values.url || '').trim();
  const siteId = String(values.siteId || '').trim();
  if (operation === 'add' && (!name || !url)) return { status: 'error', message: 'Source name and URL are required.' };
  if (operation === 'add' && (name.length > 160 || url.length > 2048 || String(values.notes || '').length > 1000)) {
    return { status: 'error', message: 'Source details are too long.' };
  }
  if (operation === 'add' && !safeExternalUrl(url)) return { status: 'error', message: 'Enter a safe public HTTP or HTTPS URL.' };
  if (operation === 'remove' && !siteId) return { status: 'error', message: 'Source identifier is required.' };
  try {
    const path = operation === 'add' ? '/api/aria/user/sources' : `/api/aria/user/sources/${encodeURIComponent(siteId)}`;
    const body = operation === 'add' ? JSON.stringify({
      name,
      url,
      site_type: values.siteType === 'website' ? 'website' : 'rss',
      notes: String(values.notes || '').trim() || undefined,
    }) : undefined;
    const raw = await request(path, { method: operation === 'add' ? 'POST' : 'DELETE', ...(body ? { body } : {}) });
    const result = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
    const confirmed = operation === 'add' ? Boolean(result.success && result.entry) : Boolean(result.success && result.deleted);
    if (!confirmed) return { status: 'error', message: `Source ${operation} could not be verified. Please try again.` };
    if (operation === 'remove') return { status: 'success', message: 'Source removed.' };
    return result.verified === true
      ? { status: 'success', message: `${name} was added and verified.` }
      : { status: 'success', message: `${name} was added and is pending verification.` };
  } catch {
    return { status: 'error', message: `Source ${operation} failed. Please try again.` };
  }
}
