import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { apiServer } from '@/lib/api';

// Server component. Uses cookies() (via apiServer) -> dynamically rendered, never at build.
export default async function DashboardPage() {
  let reportCount: number | null = null;
  let offline = false;
  try {
    // Real brain contract proven in the structural map: /api/aria/dd/reports (proxied).
    const data = await apiServer<{ reports?: unknown[] } | unknown[]>('/api/aria/dd/reports');
    const reports = Array.isArray(data) ? data : (data?.reports ?? []);
    reportCount = Array.isArray(reports) ? reports.length : 0;
  } catch {
    offline = true;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-sm text-muted-foreground">Your due-diligence, intelligence and opportunities at a glance.</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>DD Reports</CardTitle>
            <CardDescription>From your ARIA brain</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold">
              {offline ? '—' : reportCount}
            </p>
            {offline ? (
              <p className="mt-2 text-xs text-muted-foreground">Backend unreachable — showing placeholder.</p>
            ) : null}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Watchlist</CardTitle>
            <CardDescription>Tracked entities</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold text-muted-foreground">P1</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Opportunities</CardTitle>
            <CardDescription>Signal-backed</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold text-muted-foreground">P1</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
