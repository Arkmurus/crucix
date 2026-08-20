'use server';

// Server actions for authenticated customer mutations. Each runs server-side and uses
// apiServer(), which injects the JWT from the httpOnly cookie as a Bearer to the backend
// (server.mjs). The browser never handles the token. Contracts verified against the live
// static pages (see docs/aria_app_nextjs_buildout_2026_06_30.md).

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
import { apiServer } from '@/lib/api';
import { submitDD, type DDSubmissionState } from '@/lib/dd-submission';

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
export async function addWatchlist(formData: FormData) {
  const name = String(formData.get('name') || '').trim();
  if (!name) return;
  const entity_type = String(formData.get('entity_type') || '').trim() || undefined;
  const jurisdiction = String(formData.get('jurisdiction') || '').trim() || undefined;
  await apiServer('/api/aria/dd/watchlist', {
    method: 'POST',
    body: JSON.stringify({ name, entity_type, jurisdiction }),
  }).catch(() => {});
  revalidatePath('/watchlist');
}

export async function removeWatchlist(formData: FormData) {
  const name = String(formData.get('name') || '').trim();
  if (!name) return;
  await apiServer(`/api/aria/dd/watchlist/${encodeURIComponent(name)}`, { method: 'DELETE' }).catch(() => {});
  revalidatePath('/watchlist');
}

export async function rescreenWatchlist() {
  await apiServer('/api/aria/dd/watchlist/rescreen', { method: 'POST', body: '{}' }).catch(() => {});
  revalidatePath('/watchlist');
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
