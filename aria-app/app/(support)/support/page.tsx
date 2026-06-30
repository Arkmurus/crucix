import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export default function SupportOverviewPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Support console</h1>
        <p className="text-sm text-muted-foreground">
          Read customer accounts, usage and tickets. View-as is audited and read-only (P3).
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Open tickets</CardTitle>
            <CardDescription>Awaiting response</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold text-muted-foreground">P3</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Active customers</CardTitle>
            <CardDescription>Paying accounts</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold text-muted-foreground">P3</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
