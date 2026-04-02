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
  activeTab: 'tenders' | 'ideas' | 'pipeline' | 'strategy' | 'brain' = 'tenders';
  stageOptions = STAGES;
  updatingStage: string | null = null;

  constructor(private api: CrucixApiService) {}

  ngOnInit(): void {
    this.api.getBDIntelligence().subscribe(res => {
      this.bd = res;
      this.loading = false;
      // Auto-select first tab with data
      if (res?.brain) this.activeTab = 'brain';
      else if (!res?.tenders?.length && res?.ideas?.length) this.activeTab = 'ideas';
      else if (!res?.tenders?.length && !res?.ideas?.length && res?.pipeline?.length) this.activeTab = 'pipeline';
    });
  }

  get tenders() { return this.bd?.tenders || []; }
  get activeTenders() { return this.tenders.filter((t: any) => t.type === 'TENDER'); }
  get contracts() { return this.tenders.filter((t: any) => t.type === 'CONTRACT'); }
  get budgetSignals() { return this.tenders.filter((t: any) => t.type === 'BUDGET'); }
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
    if (urgency === 'HIGH') return '#f44336';
    if (urgency === 'MEDIUM') return '#ff9800';
    return '#78909c';
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

  updateStage(deal: any, stage: string): void {
    this.updatingStage = deal.id;
    this.api.updateDealStage(deal.id, stage).subscribe(() => {
      deal.stage = stage;
      this.updatingStage = null;
    });
  }
}
