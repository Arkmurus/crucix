import { Component, OnInit } from '@angular/core';
import { CrucixApiService } from '../../services/crucix-api.service';

@Component({
  selector: 'app-sources',
  templateUrl: './sources.component.html',
  styleUrls: ['./sources.component.scss']
})
export class SourcesComponent implements OnInit {
  sources: any[] = [];
  loading = true;
  displayedColumns = ['name', 'reliability', 'status', 'avgMs', 'totalOk', 'totalFail'];

  constructor(private api: CrucixApiService) {}

  ngOnInit(): void {
    this.api.getSourceHealth().subscribe(res => {
      this.sources = res?.sources || [];
      this.loading = false;
    });
  }

  get healthy(): number { return this.sources.filter(s => s.reliability === null || s.reliability >= 80).length; }
  get degraded(): number { return this.sources.filter(s => s.reliability !== null && s.reliability < 80 && s.reliability >= 50).length; }
  get critical(): number { return this.sources.filter(s => s.reliability !== null && s.reliability < 50).length; }

  statusColor(reliability: number | null): string {
    if (reliability === null) return '#9e9e9e';
    if (reliability >= 80) return '#4caf50';
    if (reliability >= 50) return '#ff9800';
    return '#f44336';
  }
}
