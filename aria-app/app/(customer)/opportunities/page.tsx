import { TrendingUp } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { PageHeader, EmptyState, UnavailableState } from '@/components/page-header';
import { tryApiServer } from '@/lib/api';
import { pickFirst, riskVariant, titleCase } from '@/lib/format';
import { normalizeOpportunities } from '@/lib/opportunities';

export const dynamic = 'force-dynamic';

export default async function OpportunitiesPage() {
  const { data, error } = await tryApiServer('/api/opportunities');
  const opps = normalizeOpportunities(data);

  return (
    <div data-aria-surface="next-customer-opportunities">
      <PageHeader title="Opportunities" description="Signal-backed market opportunities detected from the live OSINT sweep." />
      {error ? (
        <UnavailableState title="Opportunities unavailable" />
      ) : opps.length === 0 ? (
        <EmptyState title="No opportunities right now" hint="ARIA surfaces opportunities as the sweep correlates new signals." />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {opps.map((o, i) => {
            const title = o.market || titleCase(o.type) || 'Opportunity';
            const desc = pickFirst(o.notes, o.summary);
            return (
              <Card key={i}>
                <CardHeader className="flex-row items-start justify-between gap-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <TrendingUp className="h-4 w-4 text-primary" /> {title}
                  </CardTitle>
                  <div className="flex shrink-0 gap-2">
                    {o.complianceStatus ? <Badge variant={riskVariant(o.complianceStatus)}>{titleCase(o.complianceStatus)}</Badge> : null}
                    {typeof o.score === 'number' ? <Badge variant="secondary">Score {o.score}</Badge> : null}
                  </div>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  {desc ? <p className="text-muted-foreground">{String(desc)}</p> : null}
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    {o.tier ? <span>Tier: {titleCase(o.tier)}</span> : null}
                    {typeof o.explorerSignals === 'number' ? <span>{o.explorerSignals} signals</span> : null}
                    {o.conflict?.events ? <span>{o.conflict.events} conflict events</span> : null}
                    {Array.isArray(o.procurementNeeds) && o.procurementNeeds.length ? <span>{o.procurementNeeds.length} procurement needs</span> : null}
                  </div>
                  {o.sources.length ? (
                    <div className="flex flex-wrap gap-2 pt-1">
                      {o.sources.slice(0, 4).map((s, j) =>
                        s.url ? (
                          <a key={j} href={s.url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" className="text-xs text-primary hover:underline">
                            {s.title || s.type || 'source'}
                          </a>
                        ) : null,
                      )}
                    </div>
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
