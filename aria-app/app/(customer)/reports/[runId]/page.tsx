import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Card, CardContent } from '@/components/ui/card';
import { PageHeader, EmptyState, UnavailableState } from '@/components/page-header';
import { tryApiServer } from '@/lib/api';

export const dynamic = 'force-dynamic';

interface ReportDetail { markdown?: string; canonical_entity_id?: string; status?: string; error?: string; report?: { markdown?: string } }

export default async function ReportDetailPage(props: { params: Promise<{ runId: string }> }) {
  const params = await props.params;
  const runId = decodeURIComponent(params.runId);
  const { data, error } = await tryApiServer<ReportDetail>(`/api/aria/dd/report/${encodeURIComponent(runId)}?format=markdown`);
  const markdown = data?.markdown || data?.report?.markdown || '';

  return (
    <div data-aria-surface="next-customer-report-detail">
      <Link href="/reports" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Back to reports
      </Link>
      <PageHeader title="Due-diligence report" description={`Run ${runId}`} />
      {error ? (
        <UnavailableState title="Due-diligence report unavailable" />
      ) : data?.status === 'running' ? (
        <EmptyState title="Report in progress" hint="ARIA is still completing this due-diligence run. Return to the reports list to monitor it." />
      ) : data?.status === 'failed' ? (
        <UnavailableState title="Due-diligence run failed" />
      ) : markdown ? (
        <Card>
          <CardContent className="prose prose-sm max-w-none p-6 prose-headings:font-semibold prose-headings:tracking-tight prose-a:text-primary prose-table:text-sm">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
          </CardContent>
        </Card>
      ) : (
        <EmptyState title="Report unavailable" hint="This report has no markdown content, or the backend is unreachable." />
      )}
    </div>
  );
}
