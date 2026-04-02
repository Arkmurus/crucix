import { Component, OnInit } from '@angular/core';
import { CrucixApiService } from '../../services/crucix-api.service';

@Component({
  selector: 'app-opportunities',
  templateUrl: './opportunities.component.html',
  styleUrls: ['./opportunities.component.scss']
})
export class OpportunitiesComponent implements OnInit {
  opportunities: any[] = [];
  loading = true;
  updatedAt: string | null = null;
  activeFilter: 'ALL' | 'HIGH' | 'MEDIUM' | 'WATCH' = 'ALL';
  readonly totalMarkets = 26;

  constructor(private api: CrucixApiService) {}

  ngOnInit(): void {
    this.api.getOpportunities().subscribe(res => {
      this.opportunities = res?.opportunities || [];
      this.updatedAt = res?.asOf || null;
      this.loading = false;
    });
  }

  get highCount(): number   { return this.opportunities.filter(o => o.tier === 'HIGH').length; }
  get mediumCount(): number { return this.opportunities.filter(o => o.tier === 'MEDIUM').length; }

  get filtered(): any[] {
    if (this.activeFilter === 'ALL') return this.opportunities;
    return this.opportunities.filter(o => o.tier === this.activeFilter);
  }

  tierColor(tier: string): string {
    return tier === 'HIGH' ? '#f44336' : tier === 'MEDIUM' ? '#ff9800' : '#546e7a';
  }

  tierBg(tier: string): string {
    return tier === 'HIGH' ? 'rgba(244,67,54,0.15)' : tier === 'MEDIUM' ? 'rgba(255,152,0,0.12)' : 'rgba(84,110,122,0.12)';
  }
}
