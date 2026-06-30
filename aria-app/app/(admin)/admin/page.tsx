import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export default function AdminOverviewPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Admin</h1>
        <p className="text-sm text-muted-foreground">
          Power-user control: brain, self-coding gaps, design, feature flags, users and system status.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Brain</CardTitle>
            <CardDescription>Facts · edges · state</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold text-muted-foreground">P2</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Gaps</CardTitle>
            <CardDescription>Self-coding queue</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold text-muted-foreground">P2</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>System status</CardTitle>
            <CardDescription>Build · health</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold text-muted-foreground">P2</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
