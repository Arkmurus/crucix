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
  activeTab: 'leads' | 'tenders' | 'ideas' | 'pipeline' | 'strategy' | 'brain' | 'compliance' = 'tenders';
  stageOptions = STAGES;
  updatingStage: string | null = null;
  feedbackSent: Set<string> = new Set();

  // Compliance screener state
  complianceForm = { sellerCountry: '', buyerCountry: '', productCategory: '', dealValueUSD: null as number | null };
  complianceResult: any = null;
  complianceLoading = false;
  complianceProducts: any[] = [];

  // Share brief
  generatingShare = false;
  shareUrl: string | null = null;

  // Outcome modal state
  outcomeModal: { deal: any; visible: boolean } = { deal: null, visible: false };
  outcomeForm = { outcome: 'WON' as 'WON' | 'LOST' | 'NO_BID', reason: '' };
  submittingOutcome = false;

  constructor(private api: CrucixApiService) {}

  ngOnInit(): void {
    this.api.getBDIntelligence().subscribe(res => {
      this.bd = res;
      this.loading = false;
      // Auto-select most useful tab
      const hasLeads = res?.brain?.salesLeads?.length > 0 || res?.tenders?.some((t: any) => t.leadQuality === 'HOT' || t.leadQuality === 'WARM');
      if (hasLeads) this.activeTab = 'leads';
      else if (res?.brain) this.activeTab = 'brain';
      else if (!res?.tenders?.length && res?.ideas?.length) this.activeTab = 'ideas';
      else if (!res?.tenders?.length && !res?.ideas?.length && res?.pipeline?.length) this.activeTab = 'pipeline';
    });
    this.api.getComplianceProducts().subscribe(p => { this.complianceProducts = p || []; });
  }

  get tenders() { return this.bd?.tenders || []; }
  get activeTenders() { return this.tenders.filter((t: any) => t.type === 'TENDER'); }
  get contracts() { return this.tenders.filter((t: any) => t.type === 'CONTRACT'); }
  get budgetSignals() { return this.tenders.filter((t: any) => t.type === 'BUDGET'); }
  get hotTenders() { return this.tenders.filter((t: any) => t.leadQuality === 'HOT'); }
  get warmTenders() { return this.tenders.filter((t: any) => t.leadQuality === 'WARM'); }
  get brainLeads() { return this.brain?.salesLeads || []; }
  get hotLeads() { return this.brainLeads.filter((l: any) => l.urgency === 'HOT'); }
  get warmLeads() { return this.brainLeads.filter((l: any) => l.urgency === 'WARM'); }
  get coldLeads() { return this.brainLeads.filter((l: any) => l.urgency === 'COLD'); }
  get totalLeads() { return this.brainLeads.length + this.hotTenders.length + this.warmTenders.length; }
  get ideas() { return this.bd?.ideas || []; }
  get pipeline() { return this.bd?.pipeline || []; }
  get strategy() { return this.bd?.strategy || null; }
  get brain() { return this.bd?.brain || null; }
  get learning() { return this.bd?.learning || null; }
  get counts() { return this.bd?.counts || {}; }

  tierColor(priority: string): string {
    return priority === 'HIGH' ? '#f44336' : priority === 'MEDIUM' ? '#ff9800' : '#78909c';
  }

  winProbColor(prob: number): string {
    if (prob >= 70) return '#4caf50';
    if (prob >= 45) return '#ff9800';
    return '#ef5350';
  }

  urgencyColor(urgency: string): string {
    if (urgency === 'HIGH' || urgency === 'HOT') return '#f44336';
    if (urgency === 'MEDIUM' || urgency === 'WARM') return '#ff9800';
    return '#78909c';
  }

  leadQualityColor(q: string): string {
    if (q === 'HOT') return '#f44336';
    if (q === 'WARM') return '#ff9800';
    return '#78909c';
  }

  leadQualityIcon(q: string): string {
    if (q === 'HOT') return '🔥';
    if (q === 'WARM') return '⚡';
    return '👁️';
  }

  typeColor(type: string): string {
    return type === 'TENDER' ? '#e53935' : type === 'CONTRACT' ? '#43a047' : '#1976d2';
  }

  stageColor(stage: string): string {
    const map: Record<string, string> = {
      IDENTIFIED: '#546e7a', QUALIFYING: '#1976d2', ENGAGED: '#7b1fa2',
      PROPOSAL: '#f57c00', NEGOTIATING: '#ff6f00', WON: '#2e7d32', LOST: '#b71c1c'
    };
    return map[stage] || '#546e7a';
  }

  complianceStatusColor(status: string): string {
    if (status === 'PROHIBITED') return '#b71c1c';
    if (status === 'REQUIRES_APPROVAL') return '#f57c00';
    return '#2e7d32';
  }

  updateStage(deal: any, stage: string): void {
    this.updatingStage = deal.id;
    this.api.updateDealStage(deal.id, stage).subscribe(() => {
      deal.stage = stage;
      this.updatingStage = null;
      // If terminal stage, prompt for outcome capture
      if (stage === 'WON' || stage === 'LOST') {
        this.outcomeModal = { deal, visible: true };
        this.outcomeForm = { outcome: stage as 'WON' | 'LOST', reason: '' };
      }
    });
  }

  openOutcomeModal(deal: any): void {
    this.outcomeModal = { deal, visible: true };
    this.outcomeForm = { outcome: 'WON', reason: '' };
  }

  closeOutcomeModal(): void {
    this.outcomeModal = { deal: null, visible: false };
  }

  submitOutcome(): void {
    if (!this.outcomeModal.deal) return;
    this.submittingOutcome = true;
    const deal = this.outcomeModal.deal;
    this.api.recordDealOutcome(deal.id, deal.market || deal.name || 'Unknown', deal.type || 'TENDER', this.outcomeForm.outcome, this.outcomeForm.reason)
      .subscribe(() => {
        this.submittingOutcome = false;
        this.closeOutcomeModal();
      });
  }

  sendFeedback(lead: any, feedback: 'positive' | 'negative'): void {
    const key = lead.lead || lead.text || lead.title || '';
    if (this.feedbackSent.has(key)) return;
    this.feedbackSent.add(key);
    this.api.sendLeadFeedback(key, lead.market || '', feedback).subscribe();
  }

  isFeedbackSent(lead: any): boolean {
    const key = lead.lead || lead.text || lead.title || '';
    return this.feedbackSent.has(key);
  }

  screenCompliance(): void {
    if (!this.complianceForm.sellerCountry || !this.complianceForm.buyerCountry || !this.complianceForm.productCategory) return;
    this.complianceLoading = true;
    this.complianceResult = null;
    this.api.screenCompliance(
      this.complianceForm.sellerCountry.toUpperCase(),
      this.complianceForm.buyerCountry.toUpperCase(),
      this.complianceForm.productCategory,
      this.complianceForm.dealValueUSD || undefined
    ).subscribe(res => {
      this.complianceResult = res;
      this.complianceLoading = false;
    });
  }

  clearCompliance(): void {
    this.complianceResult = null;
    this.complianceForm = { sellerCountry: '', buyerCountry: '', productCategory: '', dealValueUSD: null };
  }

  generateShareLink(): void {
    this.generatingShare = true;
    this.api.createShareBrief().subscribe(res => {
      this.generatingShare = false;
      if (res?.url) {
        this.shareUrl = res.url;
        try { navigator.clipboard.writeText(res.url); } catch {}
      }
    });
  }
}
