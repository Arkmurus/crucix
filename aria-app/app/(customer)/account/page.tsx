import { Check, CreditCard, AlertCircle } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { PageHeader } from '@/components/page-header';
import { tryApiServer } from '@/lib/api';
import { titleCase } from '@/lib/format';
import { startCheckout, openPortal } from '@/lib/actions';

export const dynamic = 'force-dynamic';

interface Me { email?: string; username?: string; fullName?: string; role?: string; tier?: string }
// R-F2880 — resolve the symbol from the payload's `currency`. This file rendered
// `$${priceUsd}` while the amounts were GBP: the field NAME said USD, the product
// is priced in £, and the UI printed a dollar sign. An unmapped currency returns
// its ISO code rather than a guessed symbol — a wrong symbol on a price is a money
// error, and showing none is the honest failure direction.
function sym(c?: string): string {
  const MAP: Record<string, string> = { GBP: '£', USD: '$', EUR: '€' };
  if (!c) return '';
  return MAP[c] || `${c} `;
}

interface Tier { id: string; label: string; priceAmount: number; currency?: string; messagesPerDay?: number; ddRunsPerMonth?: number; deepResearchEnabled?: boolean; autonomousEnabled?: boolean; publicApiEnabled?: boolean; checkoutReady?: boolean }
interface BillingConfig { configured?: boolean; tiers?: Tier[] }
interface BillingMe {
  tier?: string; tierLabel?: string; priceAmount?: number; currency?: string; subscriptionStatus?: string;
  subscriptionPeriodEnd?: string | number; cancelAtPeriodEnd?: boolean; billingActive?: boolean;
  usage?: { messages?: number; uploads?: number; ddRuns?: number; caps?: { messages?: number; uploads?: number; ddRuns?: number } };
}

const TIER_ORDER = ['free', 'pro', 'proIntel'];

export default async function AccountPage(props: { searchParams: Promise<{ billing?: string }> }) {
  const searchParams = await props.searchParams;
  const [meR, cfgR, bmR] = await Promise.all([
    tryApiServer<Me>('/api/auth/me'),
    tryApiServer<BillingConfig>('/api/billing/config'),
    tryApiServer<BillingMe>('/api/billing/me'),
  ]);
  const me = meR.data || {};
  const cfg = cfgR.data || {};
  const bm = bmR.data || {};
  const currentTier = bm.tier || me.tier || 'free';
  const usage = bm.usage || {};
  const caps = usage.caps || {};

  return (
    <div>
      <PageHeader title="Account & Billing" description="Your profile, plan and usage." />

      {searchParams?.billing === 'error' ? (
        <div className="mb-6 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" /> Billing action couldn’t complete. Please try again.
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>Profile</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <Row label="Name" value={me.fullName || me.username || '—'} />
            <Row label="Email" value={me.email || '—'} />
            <Row label="Role" value={titleCase(me.role || 'customer')} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Current plan</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Plan</span>
              <Badge variant="default">{bm.tierLabel || titleCase(currentTier)}</Badge>
            </div>
            <Row label="Price" value={typeof bm.priceAmount === 'number' ? `${sym(bm.currency)}${bm.priceAmount}/mo` : '—'} />
            <Row label="Status" value={bm.subscriptionStatus ? titleCase(bm.subscriptionStatus) : 'Free'} />
            {bm.billingActive ? (
              <form action={openPortal} className="pt-2">
                <Button type="submit" variant="outline" className="w-full gap-2">
                  <CreditCard className="h-4 w-4" /> Manage subscription
                </Button>
              </form>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Usage this period</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <UsageBar label="Messages" used={usage.messages} cap={caps.messages} />
            <UsageBar label="DD runs" used={usage.ddRuns} cap={caps.ddRuns} />
            <UsageBar label="Uploads" used={usage.uploads} cap={caps.uploads} />
          </CardContent>
        </Card>
      </div>

      <h2 className="mb-3 mt-8 text-lg font-semibold tracking-tight">Plans</h2>
      {cfg.configured === false ? (
        <p className="text-sm text-muted-foreground">Billing isn’t configured yet. Contact support to upgrade.</p>
      ) : (
        <div className="grid gap-4 md:grid-cols-3">
          {(cfg.tiers || []).map((t) => {
            const isCurrent = t.id === currentTier;
            const isUpgrade = TIER_ORDER.indexOf(t.id) > TIER_ORDER.indexOf(currentTier);
            return (
              <Card key={t.id} className={isCurrent ? 'border-primary' : ''}>
                <CardHeader>
                  <CardTitle className="flex items-center justify-between text-base">
                    {t.label}
                    {isCurrent ? <Badge variant="success">Current</Badge> : null}
                  </CardTitle>
                  <p className="text-2xl font-semibold">{sym(t.currency)}{t.priceAmount}<span className="text-sm font-normal text-muted-foreground">/mo</span></p>
                </CardHeader>
                <CardContent className="space-y-3">
                  <ul className="space-y-1.5 text-sm text-muted-foreground">
                    {typeof t.messagesPerDay === 'number' ? <Feature text={`${t.messagesPerDay} messages/day`} /> : null}
                    {typeof t.ddRunsPerMonth === 'number' ? <Feature text={`${t.ddRunsPerMonth} DD runs/month`} /> : null}
                    {t.deepResearchEnabled ? <Feature text="Deep research" /> : null}
                    {t.autonomousEnabled ? <Feature text="Autonomous intelligence" /> : null}
                    {t.publicApiEnabled ? <Feature text="Public API access" /> : null}
                  </ul>
                  {isUpgrade && t.id !== 'free' && currentTier === 'free' ? (
                    <form action={startCheckout}>
                      <input type="hidden" name="tier" value={t.id} />
                      <Button type="submit" className="w-full" disabled={t.checkoutReady === false}>
                        {t.checkoutReady === false ? 'Unavailable' : `Upgrade to ${t.label}`}
                      </Button>
                    </form>
                  ) : null}
                  {isUpgrade && t.id !== 'free' && currentTier !== 'free' ? (
                    <form action={openPortal}>
                      <Button type="submit" variant="outline" className="w-full">
                        Change plan in billing portal
                      </Button>
                    </form>
                  ) : null}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}

function Feature({ text }: { text: string }) {
  return (
    <li className="flex items-center gap-2">
      <Check className="h-4 w-4 text-primary" /> {text}
    </li>
  );
}

function UsageBar({ label, used, cap }: { label: string; used?: number; cap?: number }) {
  const u = used ?? 0;
  const c = cap ?? 0;
  const pct = c > 0 ? Math.min(100, Math.round((u / c) * 100)) : 0;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-sm">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-medium">{u}{c > 0 ? ` / ${c}` : ''}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div className={`h-full rounded-full ${pct >= 90 ? 'bg-destructive' : 'bg-primary'}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
