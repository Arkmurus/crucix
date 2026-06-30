'use client';

import { useEffect, useState } from 'react';
import {
  TrendingUp, Flame, FileText, Lightbulb, Cpu, RefreshCw, ArrowRight,
  ExternalLink, Target, AlertCircle, Sparkles, Info,
} from 'lucide-react';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip as RTooltip, Cell } from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { cn } from '@/lib/utils';

// ── types (tolerant — real backend shapes from bd-intelligence) ──
interface Tender { market?: string; leadQuality?: string; type?: string; score?: number; winProbability?: number; title?: string; summary?: string; url?: string; timestamp?: string }
interface Idea { market?: string; type?: string; score?: number; signals?: number; devFinance?: boolean; rationale?: string; actionStep?: string; lusophone?: boolean }
interface Lead { lead?: string; market?: string; signalCount?: number; signalSummary?: string }
interface Brain {
  weeklyPriority?: { action?: string; whyNow?: string; firstStep?: string; market?: string };
  actionPlan?: { market?: string; deal?: string; winProb?: number; steps?: string[]; deadline?: string }[];
  marketIntelligence?: { market?: string; signal?: string; recommendation?: string }[];
  knowledgeGaps?: { question?: string; howToFind?: string }[];
  selfLearning?: { strategyAdjustment?: string; nextSweepFocus?: string; patternsObserved?: string[] };
  confidence?: number; salesLeads?: Lead[];
}
interface BDData { tenders?: Tender[]; ideas?: Idea[]; brain?: Brain | null }
interface Deal { title?: string; company?: string; market?: string; country?: string; stage?: string; value?: number; winProb?: number }

function token() {
  try { return localStorage.getItem('crucix_token'); } catch { return null; }
}
async function getJSON<T>(path: string): Promise<T | null> {
  const t = token();
  try {
    const r = await fetch(path, { headers: t ? { authorization: `Bearer ${t}` } : {}, cache: 'no-store' });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch { return null; }
}

const qBadge = (q?: string): React.ComponentProps<typeof Badge>['variant'] =>
  q === 'HOT' ? 'destructive' : q === 'WARM' ? 'warning' : q === 'SIGNAL' ? 'default' : 'muted';

function scoreColor(s: number) { return s >= 70 ? 'text-emerald-600' : s >= 40 ? 'text-amber-600' : 'text-muted-foreground'; }

export default function BDPreview() {
  const [data, setData] = useState<BDData | null>(null);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [loading, setLoading] = useState(true);
  const [authed, setAuthed] = useState(true);

  async function load() {
    setLoading(true);
    setAuthed(!!token());
    const [bd, pipe] = await Promise.all([getJSON<BDData>('/api/bd-intelligence'), getJSON<Deal[]>('/api/bd-intelligence/pipeline')]);
    setData(bd || { tenders: [], ideas: [], brain: null });
    setDeals(Array.isArray(pipe) ? pipe : []);
    setLoading(false);
  }
  useEffect(() => { load(); }, []);

  const tenders = data?.tenders ?? [];
  const ideas = data?.ideas ?? [];
  const brain = data?.brain ?? null;
  const brainLeads: Lead[] = brain?.salesLeads ?? [];
  const hotWarm = tenders.filter((t) => t.leadQuality === 'HOT' || t.leadQuality === 'WARM');
  const leads = [...hotWarm.map((t) => ({ lead: t.title, market: t.market, signalCount: 0, signalSummary: t.summary, _q: t.leadQuality })),
                 ...brainLeads.map((l) => ({ ...l, _q: 'SIGNAL' as const }))];
  const hot = tenders.filter((t) => t.leadQuality === 'HOT').length;

  const chartData = [...tenders.map((t) => ({ name: t.market || t.title || '—', score: t.score || 0, kind: 'Tender' })),
                     ...ideas.map((i) => ({ name: i.market || '—', score: i.score || 0, kind: 'Idea' }))]
    .filter((d) => d.score > 0).sort((a, b) => b.score - a.score).slice(0, 7);

  const stats = [
    { label: 'Sales Leads', value: leads.length, icon: TrendingUp, accent: 'bg-primary' },
    { label: 'Tenders', value: tenders.length, icon: FileText, accent: 'bg-blue-500' },
    { label: 'HOT Priority', value: hot, icon: Flame, accent: 'bg-destructive' },
    { label: 'Strategic Ideas', value: ideas.length, icon: Lightbulb, accent: 'bg-amber-500' },
  ];

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-10 border-b bg-background/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-primary-foreground"><TrendingUp className="h-5 w-5" /></span>
            <div>
              <h1 className="text-lg font-semibold leading-tight tracking-tight">BD &amp; Strategy Intelligence</h1>
              <p className="text-xs text-muted-foreground">
                {loading ? 'Loading…' : `${leads.length} leads · ${tenders.length} tenders · ${ideas.length} ideas`}
              </p>
            </div>
          </div>
          <Button variant="outline" size="sm" onClick={load} className="gap-2"><RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} /> Refresh</Button>
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-6 px-6 py-6">
        {!authed && !loading ? (
          <div className="flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
            <Info className="h-4 w-4 shrink-0" /> Sign in at <strong>intel.arkmurus.com</strong> first, then reload — this preview reads your live session to load real data.
          </div>
        ) : null}

        {/* Stat tiles */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {stats.map((s) => {
            const Icon = s.icon;
            return (
              <Card key={s.label} className="overflow-hidden">
                <div className={cn('h-1', s.accent)} />
                <CardContent className="flex items-center justify-between p-5">
                  <div>
                    <p className="text-sm text-muted-foreground">{s.label}</p>
                    {loading ? <Skeleton className="mt-2 h-8 w-12" /> : <p className="mt-1 text-3xl font-semibold tabular-nums">{s.value}</p>}
                  </div>
                  <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted text-foreground/70"><Icon className="h-5 w-5" /></span>
                </CardContent>
              </Card>
            );
          })}
        </div>

        {/* Chart */}
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Target className="h-4 w-4 text-primary" /> Top opportunities by score</CardTitle></CardHeader>
          <CardContent>
            {loading ? <Skeleton className="h-56 w-full" /> : chartData.length === 0 ? (
              <Empty title="No scored opportunities yet" hint="Scores populate after the next intelligence sweep." />
            ) : (
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={chartData} layout="vertical" margin={{ left: 8, right: 24 }}>
                  <XAxis type="number" domain={[0, 100]} hide />
                  <YAxis type="category" dataKey="name" width={130} tick={{ fontSize: 12, fill: 'hsl(var(--muted-foreground))' }} tickLine={false} axisLine={false} />
                  <RTooltip cursor={{ fill: 'hsl(var(--muted))' }} contentStyle={{ borderRadius: 8, border: '1px solid hsl(var(--border))', fontSize: 12 }} />
                  <Bar dataKey="score" radius={[0, 6, 6, 0]} barSize={18}>
                    {chartData.map((d, i) => <Cell key={i} fill={d.score >= 70 ? '#059669' : d.score >= 40 ? '#d97706' : '#a78bfa'} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        {/* Tabs */}
        <Tabs defaultValue="strategy">
          <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1 bg-transparent p-0">
            <TabTrig value="strategy" icon={Cpu} label="Brain Strategy" />
            <TabTrig value="leads" icon={Sparkles} label="Sales Leads" count={leads.length} />
            <TabTrig value="tenders" icon={FileText} label="Tenders" count={tenders.length} />
            <TabTrig value="ideas" icon={Lightbulb} label="Ideas" count={ideas.length} />
            <TabTrig value="pipeline" icon={Target} label="Pipeline" count={deals.length} />
          </TabsList>

          {/* STRATEGY */}
          <TabsContent value="strategy" className="mt-5 space-y-4">
            {loading ? <CardSkeleton /> : !brain ? (
              <Empty title="Brain strategy not available" hint="Requires the LLM configured and a completed sweep." />
            ) : (
              <>
                <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                  <Info className="mt-0.5 h-4 w-4 shrink-0" /> AI-generated from live signals. Named buyers, deal values and dates are estimates — verify before acting.
                </div>
                {brain.weeklyPriority?.action ? (
                  <Card className="border-l-4 border-l-destructive">
                    <CardContent className="p-5">
                      <div className="mb-2 flex items-center gap-2"><Badge variant="destructive">Top priority</Badge>{brain.weeklyPriority.market ? <Badge variant="secondary">{brain.weeklyPriority.market}</Badge> : null}</div>
                      <p className="font-semibold">{brain.weeklyPriority.action}</p>
                      {brain.weeklyPriority.whyNow ? <p className="mt-1 text-sm text-muted-foreground"><span className="font-medium text-foreground">Why now:</span> {brain.weeklyPriority.whyNow}</p> : null}
                      {brain.weeklyPriority.firstStep ? <NextStep text={brain.weeklyPriority.firstStep} /> : null}
                    </CardContent>
                  </Card>
                ) : null}
                {brain.actionPlan?.length ? (
                  <Section title="Action plan">
                    {brain.actionPlan.map((a, i) => (
                      <Card key={i}>
                        <CardContent className="p-5">
                          <div className="mb-2 flex flex-wrap items-center gap-2">
                            {a.market ? <Badge variant="secondary">{a.market}</Badge> : null}
                            {typeof a.winProb === 'number' ? <Badge variant="success">Win {a.winProb}%</Badge> : null}
                          </div>
                          <p className="font-medium">{a.deal}</p>
                          {a.steps?.length ? <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-muted-foreground">{a.steps.map((s, j) => <li key={j}>{s}</li>)}</ol> : null}
                          {a.deadline ? <p className="mt-2 text-sm"><span className="font-medium">Deadline:</span> <span className="text-muted-foreground">{a.deadline}</span></p> : null}
                        </CardContent>
                      </Card>
                    ))}
                  </Section>
                ) : null}
                {brain.marketIntelligence?.length ? (
                  <Section title="Market intelligence">
                    {brain.marketIntelligence.map((m, i) => (
                      <Card key={i}><CardContent className="p-4">
                        <div className="mb-1 flex items-center gap-2"><Badge variant="secondary">{m.market}</Badge></div>
                        <p className="text-sm font-medium">{m.signal}</p>
                        {m.recommendation ? <p className="mt-1 text-sm text-muted-foreground">{m.recommendation}</p> : null}
                      </CardContent></Card>
                    ))}
                  </Section>
                ) : null}
                {brain.knowledgeGaps?.length ? (
                  <Section title="Knowledge gaps">
                    {brain.knowledgeGaps.map((g, i) => (
                      <Card key={i} className="border-l-4 border-l-amber-400"><CardContent className="p-4">
                        <p className="flex items-start gap-2 text-sm font-medium"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />{g.question}</p>
                        {g.howToFind ? <p className="mt-1 pl-6 text-sm text-muted-foreground">{g.howToFind}</p> : null}
                      </CardContent></Card>
                    ))}
                  </Section>
                ) : null}
                {typeof brain.confidence === 'number' ? <p className="text-right text-xs text-muted-foreground">Brain confidence: {Math.round(brain.confidence * 100)}%</p> : null}
              </>
            )}
          </TabsContent>

          {/* LEADS */}
          <TabsContent value="leads" className="mt-5">
            {loading ? <GridSkeleton /> : leads.length === 0 ? (
              <Empty title="No qualified leads yet" hint="Leads appear when tenders score HOT/WARM or the brain surfaces signal-backed leads." />
            ) : (
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {leads.map((l, i) => (
                  <Card key={i} className="transition-shadow hover:shadow-md">
                    <CardContent className="p-5">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <span className="font-medium">{l.market || 'Global'}</span>
                        <Badge variant={qBadge((l as { _q?: string })._q)}>{(l as { _q?: string })._q === 'SIGNAL' ? 'Signal' : (l as { _q?: string })._q}</Badge>
                      </div>
                      {l.signalCount ? <p className="mb-2 text-2xl font-semibold text-primary tabular-nums">{l.signalCount}<span className="ml-1 text-xs font-normal text-muted-foreground">signals</span></p> : null}
                      <p className="text-sm font-medium leading-snug">{l.lead || l.market}</p>
                      {l.signalSummary ? <p className="mt-2 border-t pt-2 text-sm text-muted-foreground line-clamp-4">{l.signalSummary}</p> : null}
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </TabsContent>

          {/* TENDERS */}
          <TabsContent value="tenders" className="mt-5">
            {loading ? <GridSkeleton /> : tenders.length === 0 ? (
              <Empty title="No tenders found" hint="Tenders are extracted from procurement feeds during sweeps." />
            ) : (
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {tenders.map((t, i) => (
                  <Card key={i} className="flex flex-col transition-shadow hover:shadow-md">
                    <CardContent className="flex flex-1 flex-col p-5">
                      <div className="mb-3 flex items-center justify-between gap-2">
                        <span className="font-medium">{t.market || 'Global'}</span>
                        <Badge variant="secondary">{t.type || 'Tender'}</Badge>
                      </div>
                      <ScoreRow score={t.score || 0} />
                      {t.leadQuality ? <div className="mt-2"><Badge variant={qBadge(t.leadQuality)}>{t.leadQuality}</Badge>{typeof t.winProbability === 'number' ? <Badge variant="success" className="ml-2">Fit {t.winProbability}</Badge> : null}</div> : null}
                      <p className="mt-3 text-sm font-medium leading-snug">{t.title}</p>
                      {t.summary ? <p className="mt-2 border-t pt-2 text-sm text-muted-foreground line-clamp-3">{t.summary}</p> : null}
                      {t.url ? <a href={t.url} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-sm text-primary hover:underline"><ExternalLink className="h-3.5 w-3.5" /> Source</a> : null}
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </TabsContent>

          {/* IDEAS */}
          <TabsContent value="ideas" className="mt-5">
            {loading ? <GridSkeleton /> : ideas.length === 0 ? (
              <Empty title="No strategic ideas" hint="Ideas are generated from multi-source intelligence convergence." />
            ) : (
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {ideas.map((idea, i) => (
                  <Card key={i} className="transition-shadow hover:shadow-md">
                    <CardContent className="p-5">
                      <div className="mb-3 flex items-center justify-between gap-2">
                        <span className="font-medium">{idea.lusophone ? '🏳️ ' : ''}{idea.market}</span>
                        <Badge variant="warning">{idea.type || 'Idea'}</Badge>
                      </div>
                      <ScoreRow score={idea.score || 0} />
                      <p className="mt-2 text-xs text-muted-foreground">{idea.signals || 0} signals{idea.devFinance ? ' · Dev finance' : ''}</p>
                      {idea.rationale ? <p className="mt-2 border-t pt-2 text-sm text-muted-foreground line-clamp-4">{idea.rationale}</p> : null}
                      {idea.actionStep ? <NextStep text={idea.actionStep} /> : null}
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </TabsContent>

          {/* PIPELINE */}
          <TabsContent value="pipeline" className="mt-5">
            {loading ? <Skeleton className="h-48 w-full" /> : deals.length === 0 ? (
              <Empty title="No deals in pipeline yet" hint="Deals move here as opportunities are qualified." />
            ) : (
              <Table>
                <TableHeader><TableRow><TableHead>Deal</TableHead><TableHead>Market</TableHead><TableHead>Stage</TableHead><TableHead>Value</TableHead><TableHead className="w-40">Fit (est.)</TableHead></TableRow></TableHeader>
                <TableBody>
                  {deals.map((d, i) => (
                    <TableRow key={i}>
                      <TableCell className="font-medium">{d.title || d.company || '—'}</TableCell>
                      <TableCell className="text-muted-foreground">{d.market || d.country || '—'}</TableCell>
                      <TableCell><Badge variant="muted">{d.stage || 'prospect'}</Badge></TableCell>
                      <TableCell className="tabular-nums">{d.value ? `$${Math.round(d.value / 1e6)}M` : '—'}</TableCell>
                      <TableCell>{typeof d.winProb === 'number' ? <div className="flex items-center gap-2"><Progress value={Math.round(d.winProb * 100)} className="h-2" /><span className="text-xs tabular-nums text-muted-foreground">{Math.round(d.winProb * 100)}%</span></div> : '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </TabsContent>
        </Tabs>
      </main>
    </div>
  );
}

function TabTrig({ value, icon: Icon, label, count }: { value: string; icon: React.ElementType; label: string; count?: number }) {
  return (
    <TabsTrigger value={value} className="gap-2 rounded-md data-[state=active]:bg-primary/10 data-[state=active]:text-primary data-[state=active]:shadow-none">
      <Icon className="h-4 w-4" /> {label}
      {typeof count === 'number' ? <span className="rounded-full bg-muted px-1.5 text-xs tabular-nums">{count}</span> : null}
    </TabsTrigger>
  );
}
function ScoreRow({ score }: { score: number }) {
  return (
    <div className="flex items-center gap-3">
      <span className={cn('text-2xl font-semibold tabular-nums', scoreColor(score))}>{score}</span>
      <span className="text-xs text-muted-foreground">/ 100</span>
      <Progress value={score} className="h-2 flex-1" />
    </div>
  );
}
function NextStep({ text }: { text: string }) {
  return <div className="mt-3 flex items-start gap-2 rounded-md border-l-2 border-l-orange-400 bg-orange-50 p-2.5 text-sm text-orange-800"><ArrowRight className="mt-0.5 h-4 w-4 shrink-0" />{text}</div>;
}
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <div><h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3><div className="space-y-3">{children}</div></div>;
}
function Empty({ title, hint }: { title: string; hint?: string }) {
  return <div className="rounded-xl border border-dashed bg-card/50 p-12 text-center"><p className="text-sm font-medium">{title}</p>{hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}</div>;
}
function CardSkeleton() { return <div className="space-y-3"><Skeleton className="h-28 w-full" /><Skeleton className="h-28 w-full" /></div>; }
function GridSkeleton() { return <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-44 w-full" />)}</div>; }
