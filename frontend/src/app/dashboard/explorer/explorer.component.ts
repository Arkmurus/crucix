import { Component, OnInit } from '@angular/core';
import { CrucixApiService } from '../../services/crucix-api.service';

@Component({
  selector: 'app-explorer',
  templateUrl: './explorer.component.html',
  styleUrls: ['./explorer.component.scss']
})
export class ExplorerComponent implements OnInit {
  findings: any = null;
  loading = true;
  running = false;

  constructor(private api: CrucixApiService) {}

  ngOnInit(): void {
    this.api.getExplorerFindings().subscribe(res => {
      this.findings = res;
      this.loading = false;
    });
  }

  runExploration(): void {
    this.running = true;
    this.api.runExploration().subscribe(res => {
      if (res?.success !== false) this.findings = res;
      this.running = false;
    });
  }

  get insights(): any[] { return this.findings?.insights || this.findings?.findings?.insights || []; }
  get salesIdeas(): any[] { return this.findings?.salesIdeas || this.findings?.findings?.salesIdeas || []; }
  get newSources(): any[] { return this.findings?.newSources || this.findings?.findings?.newSources || []; }
  get runAt(): string | null { return this.findings?.runAt || this.findings?.updatedAt || null; }

  relevanceColor(r: string): string {
    return r === 'HIGH' ? '#f44336' : r === 'MEDIUM' ? '#ff9800' : '#9e9e9e';
  }

  urgencyColor(u: string): string {
    return u === 'HIGH' ? '#d32f2f' : u === 'MEDIUM' ? '#f57c00' : '#388e3c';
  }
}
