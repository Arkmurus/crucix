export type WatchlistMutationState =
  | { status: 'idle'; message: '' }
  | { status: 'success'; message: string }
  | { status: 'error'; message: string };

type RequestFn = (path: string, init?: RequestInit) => Promise<unknown>;

/** Execute one customer watchlist mutation and return an honest UI outcome. */
export async function performWatchlistMutation(
  request: RequestFn,
  operation: 'add' | 'remove' | 'rescreen',
  values: { name?: string; entityType?: string; jurisdiction?: string } = {},
): Promise<WatchlistMutationState> {
  const name = String(values.name || '').trim();
  if (operation !== 'rescreen' && !name) {
    return { status: 'error', message: 'An entity name is required.' };
  }
  const path = operation === 'add' ? '/api/aria/dd/watchlist'
    : operation === 'remove' ? `/api/aria/dd/watchlist/${encodeURIComponent(name)}`
    : '/api/aria/dd/watchlist/rescreen';
  const body = operation === 'add'
    ? JSON.stringify({ name, entity_type: values.entityType?.trim() || undefined, jurisdiction: values.jurisdiction?.trim() || undefined })
    : operation === 'rescreen' ? '{}' : undefined;
  try {
    await request(path, { method: operation === 'remove' ? 'DELETE' : 'POST', ...(body ? { body } : {}) });
    const message = operation === 'add' ? `${name} was added to the watchlist.`
      : operation === 'remove' ? `${name} was removed from the watchlist.`
      : 'Watchlist re-screen started successfully.';
    return { status: 'success', message };
  } catch {
    return { status: 'error', message: `Watchlist ${operation} failed. Please try again.` };
  }
}
