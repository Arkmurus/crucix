import { Plus, RefreshCw, Trash2 } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PageHeader, EmptyState, UnavailableState } from '@/components/page-header';
import { tryApiServer } from '@/lib/api';
import { pickFirst, fmtDate, riskVariant } from '@/lib/format';
import { addWatchlist, removeWatchlist, rescreenWatchlist } from '@/lib/actions';

export const dynamic = 'force-dynamic';

interface Entity {
  name?: string; entity_name?: string; entity_type?: string; jurisdiction?: string;
  last_risk?: string; last_checked?: string; added_at?: string;
}

export default async function WatchlistPage() {
  const { data, error } = await tryApiServer<{ watchlist?: Entity[] }>('/api/aria/dd/watchlist');
  const entities = Array.isArray(data) ? (data as Entity[]) : data?.watchlist ?? [];

  return (
    <div>
      <PageHeader title="Watchlist" description="Entities ARIA monitors for risk changes.">
        <form action={rescreenWatchlist}>
          <Button type="submit" variant="outline" className="gap-2">
            <RefreshCw className="h-4 w-4" /> Re-screen all
          </Button>
        </form>
      </PageHeader>

      <Card className="mb-6">
        <CardContent className="p-4">
          <form action={addWatchlist} className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <Input name="name" placeholder="Entity or company name…" className="sm:max-w-xs" required />
            <Input name="entity_type" placeholder="Type (optional)" className="sm:max-w-[10rem]" />
            <Input name="jurisdiction" placeholder="Jurisdiction (optional)" className="sm:max-w-[12rem]" />
            <Button type="submit" className="gap-2">
              <Plus className="h-4 w-4" /> Add to watchlist
            </Button>
          </form>
        </CardContent>
      </Card>

      {error ? (
        <UnavailableState title="Watchlist unavailable" />
      ) : entities.length === 0 ? (
        <EmptyState title="Watchlist is empty" hint="Add an entity above to start monitoring it for risk changes." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Entity</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Jurisdiction</TableHead>
              <TableHead>Last risk</TableHead>
              <TableHead>Last checked</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {entities.map((e, i) => {
              const name = pickFirst(e.name, e.entity_name) as string | undefined;
              return (
                <TableRow key={name || i}>
                  <TableCell className="font-medium">{name || 'Unnamed'}</TableCell>
                  <TableCell className="text-muted-foreground">{e.entity_type || '—'}</TableCell>
                  <TableCell className="text-muted-foreground">{e.jurisdiction || '—'}</TableCell>
                  <TableCell>{e.last_risk ? <Badge variant={riskVariant(e.last_risk)}>{e.last_risk}</Badge> : '—'}</TableCell>
                  <TableCell className="text-muted-foreground">{fmtDate(pickFirst(e.last_checked, e.added_at))}</TableCell>
                  <TableCell>
                    {name ? (
                      <form action={removeWatchlist}>
                        <input type="hidden" name="name" value={name} />
                        <button type="submit" className="text-muted-foreground hover:text-destructive" aria-label={`Remove ${name}`}>
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </form>
                    ) : null}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
