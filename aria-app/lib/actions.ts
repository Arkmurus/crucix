'use server';

// Server actions for authenticated customer mutations. Each runs server-side and uses
// apiServer(), which injects the JWT from the httpOnly cookie as a Bearer to the backend
// (server.mjs). The browser never handles the token. Contracts verified against the live
// static pages (see docs/aria_app_nextjs_buildout_2026_06_30.md).

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { apiServer } from '@/lib/api';
import { submitDD, type DDSubmissionState } from '@/lib/dd-submission';
import { performWatchlistMutation, type WatchlistMutationState } from '@/lib/watchlist-mutation';

// ── DD ───────────────────────────────────────────────────────────────────────
export async function runDD(_previousState: DDSubmissionState, formData: FormData): Promise<DDSubmissionState> {
  const result = await submitDD(apiServer, {
    name: String(formData.get('name') || ''),
    jurisdiction: String(formData.get('jurisdiction') || ''),
    mode: String(formData.get('mode') || 'standard'),
  });
  if (result.status === 'started' || result.status === 'existing') {
    revalidatePath('/reports');
    revalidatePath('/dashboard');
  }
  return result;
}

// ── Watchlist ─────────────────────────────────────────────────────────────────
export async function addWatchlist(
  _previousState: WatchlistMutationState,
  formData: FormData,
): Promise<WatchlistMutationState> {
  const result = await performWatchlistMutation(apiServer, 'add', {
    name: String(formData.get('name') || ''),
    entityType: String(formData.get('entity_type') || ''),
    jurisdiction: String(formData.get('jurisdiction') || ''),
  });
  if (result.status === 'success') revalidatePath('/watchlist');
  return result;
}

export async function removeWatchlist(
  _previousState: WatchlistMutationState,
  formData: FormData,
): Promise<WatchlistMutationState> {
  const result = await performWatchlistMutation(apiServer, 'remove', {
    name: String(formData.get('name') || ''),
  });
  if (result.status === 'success') revalidatePath('/watchlist');
  return result;
}

export async function rescreenWatchlist(
  _previousState: WatchlistMutationState,
  _formData: FormData,
): Promise<WatchlistMutationState> {
  const result = await performWatchlistMutation(apiServer, 'rescreen');
  if (result.status === 'success') revalidatePath('/watchlist');
  return result;
}

// ── Billing ───────────────────────────────────────────────────────────────────
// Both redirect to Stripe-hosted pages. Errors surface by redirecting back with ?billing=error.
export async function startCheckout(formData: FormData) {
  const tier = String(formData.get('tier') || '').trim();
  let url: string | null = null;
  try {
    const res = await apiServer<{ url?: string }>('/api/billing/checkout', {
      method: 'POST',
      body: JSON.stringify({ tier }),
    });
    url = res?.url ?? null;
  } catch {
    url = null;
  }
  redirect(url || '/account?billing=error');
}

export async function openPortal() {
  let url: string | null = null;
  try {
    const res = await apiServer<{ url?: string }>('/api/billing/portal', { method: 'POST', body: '{}' });
    url = res?.url ?? null;
  } catch {
    url = null;
  }
  redirect(url || '/account?billing=error');
}
