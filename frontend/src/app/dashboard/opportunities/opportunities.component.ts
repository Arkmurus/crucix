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
  displayedColumns = ['market', 'score', 'tier', 'conflict', 'needs', 'oems', 'compliance'];

  constructor(private api: CrucixApiService) {}

  ngOnInit(): void {
    this.api.getOpportunities().subscribe(res => {
      this.opportunities = res?.opportunities || [];
      this.updatedAt = res?.asOf || null;
      this.loading = false;
    });
  }

  tierColor(tier: string): string {
    return tier === 'HIGH' ? '#f44336' : tier === 'MEDIUM' ? '#ff9800' : '#8bc34a';
  }

  complianceColor(status: string): string {
    return status === 'CLEAR' ? 'primary' : 'warn';
  }
}
