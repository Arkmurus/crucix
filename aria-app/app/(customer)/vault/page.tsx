import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PageHeader, EmptyState, UnavailableState } from '@/components/page-header';
import { AddSourceForm, RemoveSourceForm } from '@/components/source-vault-actions';
import { tryApiServer } from '@/lib/api';
import { fmtDate, statusVariant, titleCase } from '@/lib/format';
import { normalizeUserSources } from '@/lib/source-vault';

export const dynamic = 'force-dynamic';

export default async function VaultPage() {
  const { data, error } = await tryApiServer('/api/aria/user/sources');
  const entries = normalizeUserSources(data);
  const verified = entries.filter((entry) => entry.status === 'verified').length;
  const pending = entries.filter((entry) => entry.status === 'pending').length;

  return (
    <div data-aria-surface="next-customer-vault">
      <PageHeader title="My Sources" description="Public intelligence sources you have asked ARIA to monitor." />

      {!error ? <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <StatCard label="Your sources" value={entries.length} />
        <StatCard label="Verified" value={verified} variant="success" />
        <StatCard label="Pending verification" value={pending} variant="warning" />
      </div> : null}

      <Card className="mb-6"><CardContent className="p-4"><AddSourceForm /></CardContent></Card>

      {error ? (
        <UnavailableState title="Vault sources unavailable" />
      ) : entries.length === 0 ? (
        <EmptyState title="No sources yet" hint="Add a public RSS feed or website above to start monitoring it." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Source</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Verified</TableHead>
              <TableHead>Updated</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.map((e, i) => (
              <TableRow key={e.siteId || i}>
                <TableCell>
                  <div className="font-medium">{e.name}</div>
                  {e.url ? (
                    <a href={e.url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" className="text-xs text-primary hover:underline">
                      {e.url}
                    </a>
                  ) : null}
                </TableCell>
                <TableCell>{e.status ? <Badge variant={statusVariant(e.status)}>{titleCase(e.status)}</Badge> : '—'}</TableCell>
                <TableCell className="text-muted-foreground">{titleCase(e.siteType) || '—'}</TableCell>
                <TableCell className="text-muted-foreground">{fmtDate(e.lastVerifiedAt)}</TableCell>
                <TableCell className="text-muted-foreground">{fmtDate(e.updatedAt || e.createdAt)}</TableCell>
                <TableCell><RemoveSourceForm siteId={e.siteId} name={e.name} /></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

function StatCard({ label, value, variant }: { label: string; value: number; variant?: 'success' | 'warning' | 'muted' }) {
  const color =
    variant === 'success' ? 'text-emerald-600' : variant === 'warning' ? 'text-amber-600' : variant === 'muted' ? 'text-muted-foreground' : 'text-foreground';
  return (
    <Card>
      <CardContent className="p-5">
        <p className="text-sm text-muted-foreground">{label}</p>
        <p className={`mt-1 text-3xl font-semibold ${color}`}>{value}</p>
      </CardContent>
    </Card>
  );
}
