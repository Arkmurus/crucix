import Link from 'next/link';
import { FileSearch, Eye, TrendingUp, Briefcase, ArrowRight } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { PageHeader, EmptyState, UnavailableState } from '@/components/page-header';
import { tryApiServer } from '@/lib/api';
import { pickFirst, fmtDate, riskVariant } from '@/lib/format';

export const dynamic = 'force-dynamic';

interface Report { run_id?: string; entity_name?: string; jurisdiction?: string; risk_classification?: string; severity?: string; worst_severity?: string; risk?: string; generated_at?: string; created_at?: string; timestamp?: string }

function arr<T>(v: unknown, key: string): T[] {
  if (Array.isArray(v)) return v as T[];
  if (v && typeof v === 'object' && Array.isArray((v as Record<string, unknown>)[key])) return (v as Record<string, T[]>)[key];
  return [];
}

export default async function DashboardPage() {
  const [reportsR, watchR, pipelineR, oppsR] = await Promise.all([
    tryApiServer<{ reports?: Report[] }>('/api/aria/dd/reports?limit=100'),
    tryApiServer<{ watchlist?: unknown[] }>('/api/aria/dd/watchlist'),
    tryApiServer<unknown[]>('/api/bd-intelligence/pipeline'),
    tryApiServer<{ opportunities?: unknown[] }>('/api/opportunities'),
  ]);

  const reports = arr<Report>(reportsR.data, 'reports');
  const watch = arr<unknown>(watchR.data, 'watchlist');
  const deals = arr<unknown>(pipelineR.data, 'pipeline');
  const opps = arr<unknown>(oppsR.data, 'opportunities');

  const kpis = [
    { label: 'DD Reports', value: reports.length, unavailable: Boolean(reportsR.error), icon: FileSearch, href: '/reports' },
    { label: 'Watchlist', value: watch.length, unavailable: Boolean(watchR.error), icon: Eye, href: '/watchlist' },
    { label: 'Opportunities', value: opps.length, unavailable: Boolean(oppsR.error), icon: TrendingUp, href: '/opportunities' },
    { label: 'Active Deals', value: deals.length, unavailable: Boolean(pipelineR.error), icon: Briefcase, href: '/opportunities' },
  ];

  const recent = reports.slice(0, 6);

  return (
    <div>
      <PageHeader title="Dashboard" description="Your due-diligence, intelligence and opportunities at a glance." />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {kpis.map((k) => {
          const Icon = k.icon;
          return (
            <Link key={k.label} href={k.href}>
              <Card className="transition-colors hover:border-primary/40">
                <CardContent className="flex items-center justify-between p-5">
                  <div>
                    <p className="text-sm text-muted-foreground">{k.label}</p>
                    <p className="mt-1 text-3xl font-semibold">{k.unavailable ? '-' : k.value}</p>
                    {k.unavailable ? <p className="text-xs text-amber-600">Unavailable</p> : null}
                  </div>
                  <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <Icon className="h-5 w-5" />
                  </span>
                </CardContent>
              </Card>
            </Link>
          );
        })}
      </div>

      <Card className="mt-6">
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>Recent DD reports</CardTitle>
          <Link href="/reports" className="flex items-center gap-1 text-sm text-primary hover:underline">
            View all <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </CardHeader>
        <CardContent>
          {reportsR.error ? (
            <UnavailableState title="Recent DD reports unavailable" />
          ) : recent.length === 0 ? (
            <EmptyState title="No reports yet" hint="Run due diligence from the DD Reports page to see results here." />
          ) : (
            <ul className="divide-y">
              {recent.map((r, i) => {
                const risk = pickFirst(r.severity, r.worst_severity, r.risk, r.risk_classification);
                return (
                  <li key={r.run_id || i} className="flex items-center justify-between py-3">
                    <div className="min-w-0">
                      <p className="truncate font-medium">{r.entity_name || 'Unnamed entity'}</p>
                      <p className="text-xs text-muted-foreground">
                        {r.jurisdiction || '—'} · {fmtDate(pickFirst(r.generated_at, r.created_at, r.timestamp))}
                      </p>
                    </div>
                    {risk ? <Badge variant={riskVariant(risk)}>{String(risk)}</Badge> : null}
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
