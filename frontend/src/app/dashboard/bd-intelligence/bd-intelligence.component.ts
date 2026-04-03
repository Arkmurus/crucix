import { Component, OnInit } from '@angular/core';
import { CrucixApiService } from '../../services/crucix-api.service';

const STAGES = ['IDENTIFIED', 'QUALIFYING', 'ENGAGED', 'PROPOSAL', 'NEGOTIATING', 'WON', 'LOST'];

@Component({
  selector: 'app-bd-intelligence',
  templateUrl: './bd-intelligence.component.html',
  styleUrls: ['./bd-intelligence.component.scss']
})
export class BdIntelligenceComponent implements OnInit {
  loading = true;
  bd: any = null;
  activeTab: 'leads' | 'tenders' | 'ideas' | 'pipeline' | 'strategy' | 'brain' | 'compliance' | 'mlleads' = 'tenders';
  stageOptions = STAGES;
  updatingStage: string | null = null;
  feedbackSent: Set<string> = new Set();

  mlLeads: any[] = [];
  mlLeadsLoading = true;
  brainStatus: any = null;

  complianceForm = { sellerCountry: '', buyerCountry: '', productCategory: '', dealValueUSD: null as number | null };
  complianceResult: any = null;
  complianceLoading = false;
  complianceProducts: any[] = [];

  // Card share
  cardShare = { visible: false, text: '', url: '' };
  cardShareCopied = false;

  // Outcome modal
  outcomeModal: { deal: any; visible: boolean } = { deal: null, visible: false };
  outcomeForm = { outcome: 'WON' as 'WON' | 'LOST' | 'NO_BID', reason: '' };
  submittingOutcome = false;

  constructor(private api: CrucixApiService) {}

  ngOnInit(): void {
    this.api.getBDIntelligence().subscribe(res => {
      this.bd = res;
      this.loading = false;
      const hasLeads = res?.brain?.salesLeads?.length > 0 || res?.tenders?.some((t: any) => t.leadQuality === 'HOT' || t.leadQuality === 'WARM');
      if (hasLeads) this.activeTab = 'leads';
      else if (res?.brain) this.activeTab = 'brain';
      else if (!res?.tenders?.length && res?.ideas?.length) this.activeTab = 'ideas';
      else if (!res?.tenders?.length && !res?.ideas?.length && res?.pipeline?.length) this.activeTab = 'pipeline';
    });
    this.api.getComplianceProducts().subscribe(p => { this.complianceProducts = p || []; });
    this.api.getBrainLeads().subscribe(leads => { this.mlLeads = leads || []; this.mlLeadsLoading = false; });
    this.api.getBrainStatus().subscribe(s => { this.brainStatus = s; });
  }

  // ── Getters ────────────────────────────────────────────────────────────────
  get tenders()       { return this.bd?.tenders || []; }
  get activeTenders() { return this.tenders.filter((t: any) => t.type === 'TENDER'); }
  get contracts()     { return this.tenders.filter((t: any) => t.type === 'CONTRACT'); }
  get budgetSignals() { return this.tenders.filter((t: any) => t.type === 'BUDGET'); }
  get hotTenders()    { return this.tenders.filter((t: any) => t.leadQuality === 'HOT'); }
  get warmTenders()   { return this.tenders.filter((t: any) => t.leadQuality === 'WARM'); }
  get brainLeads()    { return this.brain?.salesLeads || []; }
  get hotLeads()      { return this.brainLeads.filter((l: any) => l.urgency === 'HOT'); }
  get warmLeads()     { return this.brainLeads.filter((l: any) => l.urgency === 'WARM'); }
  get coldLeads()     { return this.brainLeads.filter((l: any) => l.urgency === 'COLD'); }
  get totalLeads()    { return this.brainLeads.length + this.hotTenders.length + this.warmTenders.length; }
  get ideas()         { return this.bd?.ideas || []; }
  get pipeline()      { return this.bd?.pipeline || []; }
  get strategy()      { return this.bd?.strategy || null; }
  get brain()         { return this.bd?.brain || null; }
  get learning()      { return this.bd?.learning || null; }
  get counts()        { return this.bd?.counts || {}; }

  // ── Color helpers ──────────────────────────────────────────────────────────
  mlWinProbPct(l: any): number { return Math.round((l?.win_probability ?? 0) * 100); }
  mlWinProbColor(l: any): string { const p = this.mlWinProbPct(l); return p >= 60 ? '#4caf50' : p >= 35 ? '#ff9800' : '#ef5350'; }
  mlUrgencyColor(u: string): string { return u === 'HIGH' ? '#f44336' : u === 'MEDIUM' ? '#ff9800' : '#78909c'; }
  tierColor(p: string): string { return p === 'HIGH' ? '#f44336' : p === 'MEDIUM' ? '#ff9800' : '#78909c'; }
  winProbColor(p: number): string { return p >= 70 ? '#4caf50' : p >= 45 ? '#ff9800' : '#ef5350'; }
  urgencyColor(u: string): string { return (u === 'HIGH' || u === 'HOT') ? '#f44336' : (u === 'MEDIUM' || u === 'WARM') ? '#ff9800' : '#78909c'; }
  typeColor(t: string): string { return t === 'TENDER' ? '#e53935' : t === 'CONTRACT' ? '#43a047' : '#1976d2'; }
  stageColor(s: string): string {
    const m: Record<string,string> = { IDENTIFIED:'#546e7a',QUALIFYING:'#1976d2',ENGAGED:'#7b1fa2',PROPOSAL:'#f57c00',NEGOTIATING:'#ff6f00',WON:'#2e7d32',LOST:'#b71c1c' };
    return m[s] || '#546e7a';
  }
  complianceStatusColor(s: string): string { return s === 'PROHIBITED' ? '#b71c1c' : s === 'REQUIRES_APPROVAL' ? '#f57c00' : '#2e7d32'; }

  // ── Card sharing ───────────────────────────────────────────────────────────
  openCardShare(text: string, url = ''): void {
    this.cardShare = { visible: true, text, url };
    this.cardShareCopied = false;
  }

  closeCardShare(): void { this.cardShare = { ...this.cardShare, visible: false }; }

  shareCardVia(platform: 'whatsapp' | 'telegram' | 'email' | 'x'): void {
    const { text, url } = this.cardShare;
    const msg = text + (url ? '\n\n' + url : '');
    const links: Record<string, string> = {
      whatsapp: `https://wa.me/?text=${encodeURIComponent(msg)}`,
      telegram: `https://t.me/share/url?url=${encodeURIComponent(url || 'https://intel.sursec.co.uk')}&text=${encodeURIComponent(text)}`,
      email:    `mailto:?subject=${encodeURIComponent('CRUCIX Intelligence')}&body=${encodeURIComponent(msg)}`,
      x:        `https://x.com/intent/tweet?text=${encodeURIComponent(text.slice(0, 240))}`,
    };
    window.open(links[platform], '_blank', 'noopener,noreferrer');
  }

  copyCardText(): void {
    const { text, url } = this.cardShare;
    navigator.clipboard.writeText(text + (url ? '\n\n' + url : '')).then(() => {
      this.cardShareCopied = true;
      setTimeout(() => { this.cardShareCopied = false; }, 2500);
    }).catch(() => {});
  }

  // ── Share text formatters ──────────────────────────────────────────────────
  fmtLead(l: any): string {
    const e = l.urgency === 'HOT' ? '🔥' : l.urgency === 'WARM' ? '⚡' : '❄️';
    const lines = [`${e} ${l.urgency || 'LEAD'} — ${l.market}`];
    if (l.lead) lines.push(l.lead);
    if (l.estimatedValue) lines.push(`💰 ${l.estimatedValue}`);
    if (l.procurementAuthority) lines.push(`🏛 Authority: ${l.procurementAuthority}`);
    if (l.oemRecommendation) lines.push(`🔩 OEM: ${l.oemRecommendation}`);
    if (l.nextStep) lines.push(`→ ${l.nextStep}`);
    lines.push('\n— CRUCIX Intelligence · intel.sursec.co.uk');
    return lines.join('\n');
  }

  fmtTender(t: any): string {
    const e = t.leadQuality === 'HOT' ? '🔥' : t.leadQuality === 'WARM' ? '⚡' : '📋';
    const lines = [`${e} ${t.type || 'TENDER'} — ${t.market}`];
    if (t.title) lines.push(t.title);
    if (t.summary && t.summary !== t.title) lines.push(t.summary.slice(0, 180));
    if (t.winProbability != null) lines.push(`Win probability: ${t.winProbability}%`);
    if (t.date) lines.push(`📅 ${t.date}`);
    lines.push('\n— CRUCIX Intelligence · intel.sursec.co.uk');
    return lines.join('\n');
  }

  fmtIdea(idea: any): string {
    const lines = [`💡 STRATEGIC IDEA — ${idea.market}`];
    if (idea.rationale) lines.push(idea.rationale);
    if (idea.actionStep) lines.push(`→ ${idea.actionStep}`);
    if (idea.type) lines.push(`Type: ${idea.type}`);
    lines.push('\n— CRUCIX Intelligence · intel.sursec.co.uk');
    return lines.join('\n');
  }

  fmtDeal(deal: any): string {
    const lines = [`📊 PIPELINE [${deal.stage}] — ${deal.market}`];
    if (deal.title) lines.push(deal.title);
    lines.push('\n— CRUCIX Intelligence · intel.sursec.co.uk');
    return lines.join('\n');
  }

  fmtMLLead(l: any): string {
    const lines = [`🤖 ML LEAD — ${l.market} [${l.urgency || 'MEDIUM'}]`];
    if (l.lead_action) lines.push(l.lead_action);
    else if (l.signal_title) lines.push(l.signal_title);
    lines.push(`Win probability: ${this.mlWinProbPct(l)}%`);
    if (l.reasoning) lines.push(l.reasoning.slice(0, 150));
    lines.push('\n— CRUCIX Intelligence · intel.sursec.co.uk');
    return lines.join('\n');
  }

  // ── Pipeline management ────────────────────────────────────────────────────
  updateStage(deal: any, stage: string): void {
    this.updatingStage = deal.id;
    this.api.updateDealStage(deal.id, stage).subscribe(() => {
      deal.stage = stage;
      this.updatingStage = null;
      if (stage === 'WON' || stage === 'LOST') {
        this.outcomeModal = { deal, visible: true };
        this.outcomeForm = { outcome: stage as 'WON' | 'LOST', reason: '' };
      }
    });
  }

  openOutcomeModal(deal: any): void { this.outcomeModal = { deal, visible: true }; this.outcomeForm = { outcome: 'WON', reason: '' }; }
  closeOutcomeModal(): void { this.outcomeModal = { deal: null, visible: false }; }

  submitOutcome(): void {
    if (!this.outcomeModal.deal) return;
    this.submittingOutcome = true;
    const deal = this.outcomeModal.deal;
    this.api.recordDealOutcome(deal.id, deal.market || deal.name || 'Unknown', deal.type || 'TENDER', this.outcomeForm.outcome, this.outcomeForm.reason)
      .subscribe(() => { this.submittingOutcome = false; this.closeOutcomeModal(); });
  }

  sendFeedback(lead: any, feedback: 'positive' | 'negative'): void {
    const key = lead.lead || lead.text || lead.title || '';
    if (this.feedbackSent.has(key)) return;
    this.feedbackSent.add(key);
    this.api.sendLeadFeedback(key, lead.market || '', feedback).subscribe();
  }

  isFeedbackSent(lead: any): boolean { return this.feedbackSent.has(lead.lead || lead.text || lead.title || ''); }

  screenCompliance(): void {
    if (!this.complianceForm.sellerCountry || !this.complianceForm.buyerCountry || !this.complianceForm.productCategory) return;
    this.complianceLoading = true;
    this.complianceResult = null;
    this.api.screenCompliance(
      this.complianceForm.sellerCountry.toUpperCase(),
      this.complianceForm.buyerCountry.toUpperCase(),
      this.complianceForm.productCategory,
      this.complianceForm.dealValueUSD || undefined
    ).subscribe(res => { this.complianceResult = res; this.complianceLoading = false; });
  }

  clearCompliance(): void {
    this.complianceResult = null;
    this.complianceForm = { sellerCountry: '', buyerCountry: '', productCategory: '', dealValueUSD: null };
  }
}
