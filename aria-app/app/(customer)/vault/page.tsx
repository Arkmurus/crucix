import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PageHeader, EmptyState, UnavailableState } from '@/components/page-header';
import { tryApiServer } from '@/lib/api';
import { fmtDate, statusVariant, titleCase } from '@/lib/format';

export const dynamic = 'force-dynamic';

interface VaultEntry {
  site_name?: string; site_id?: string; site_url?: string; agent_id?: string;
  status?: string; created_at?: string; updated_at?: string; last_verified_at?: string;
}
interface VaultResp {
  entries?: VaultEntry[];
  stats?: { total?: number; by_status?: Record<string, number>; stale_unverified?: number };
}

export default async function VaultPage() {
  const { data, error } = await tryApiServer<VaultResp>('/api/aria/vault?limit=100');
  const entries = data?.entries ?? [];
  const stats = data?.stats;

  return (
    <div>
      <PageHeader title="Vault" description="Verified intelligence sources backing ARIA's findings." />

      {stats ? (
        <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Total sources" value={stats.total ?? entries.length} />
          <StatCard label="Verified" value={stats.by_status?.verified ?? 0} variant="success" />
          <StatCard label="Needs operator" value={stats.by_status?.needs_operator ?? 0} variant="warning" />
          <StatCard label="Stale" value={stats.stale_unverified ?? 0} variant="muted" />
        </div>
      ) : null}

      {error ? (
        <UnavailableState title="Vault sources unavailable" />
      ) : entries.length === 0 ? (
        <EmptyState title="No sources yet" hint="Verified sources appear here as ARIA validates them." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Source</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Agent</TableHead>
              <TableHead>Verified</TableHead>
              <TableHead>Updated</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.map((e, i) => (
              <TableRow key={e.site_id || i}>
                <TableCell>
                  <div className="font-medium">{e.site_name || e.site_id || 'Source'}</div>
                  {e.site_url ? (
                    <a href={e.site_url} target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">
                      {e.site_url}
                    </a>
                  ) : null}
                </TableCell>
                <TableCell>{e.status ? <Badge variant={statusVariant(e.status)}>{titleCase(e.status)}</Badge> : '—'}</TableCell>
                <TableCell className="text-muted-foreground">{e.agent_id || '—'}</TableCell>
                <TableCell className="text-muted-foreground">{fmtDate(e.last_verified_at)}</TableCell>
                <TableCell className="text-muted-foreground">{fmtDate(e.updated_at || e.created_at)}</TableCell>
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
