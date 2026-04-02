import { Component, OnInit, OnDestroy } from '@angular/core';
import { Subscription, interval } from 'rxjs';
import { switchMap, startWith } from 'rxjs/operators';
import { CrucixApiService } from '../../services/crucix-api.service';
import { CrucixSseService } from '../../services/crucix-sse.service';

@Component({
  selector: 'app-intelligence',
  templateUrl: './intelligence.component.html',
  styleUrls: ['./intelligence.component.scss']
})
export class IntelligenceComponent implements OnInit, OnDestroy {
  data: any = null;
  health: any = null;
  loading = true;
  lastUpdated: Date | null = null;
  sweeping = false;

  private subs: Subscription[] = [];

  constructor(
    private api: CrucixApiService,
    private sse: CrucixSseService
  ) {}

  ngOnInit(): void {
    this.sse.connect();

    // Load initial data
    this.loadData();

    // Refresh every 5 minutes
    this.subs.push(
      interval(5 * 60 * 1000).subscribe(() => this.loadData())
    );

    // React to live SSE updates
    this.subs.push(
      this.sse.events$.subscribe(event => {
        if (event.type === 'update' && event.data) {
          this.data = event.data;
          this.lastUpdated = new Date();
          this.loading = false;
        }
        if (event.type === 'sweep_start') this.sweeping = true;
        if (event.type === 'update' || event.type === 'sweep_error') this.sweeping = false;
      })
    );

    // Load health separately
    this.loadHealth();
    this.subs.push(
      interval(60 * 1000).subscribe(() => this.loadHealth())
    );
  }

  loadData(): void {
    this.api.getData().subscribe(d => {
      if (d) { this.data = d; this.lastUpdated = new Date(); }
      this.loading = false;
    });
  }

  loadHealth(): void {
    this.api.getHealth().subscribe(h => { if (h) this.health = h; });
  }

  triggerSweep(): void {
    this.sweeping = true;
    this.api.triggerSweep().subscribe();
  }

  get vix(): number | null { return this.data?.fred?.find((f: any) => f.id === 'VIXCLS')?.value ?? null; }
  get brent(): number | null { return this.data?.energy?.brent ?? null; }
  get wti(): number | null { return this.data?.energy?.wti ?? null; }
  get direction(): string { return this.data?.delta?.summary?.direction || 'unknown'; }
  get urgentPosts(): any[] { return (this.data?.tg?.urgent || []).slice(0, 6); }
  get ideas(): any[] { return (this.data?.ideas || []).slice(0, 5); }
  get correlations(): any[] { return (this.data?.correlations || []).filter((c: any) => c.severity === 'critical' || c.severity === 'high').slice(0, 4); }
  get sourcesOk(): number { return this.data?.meta?.sourcesOk ?? 0; }
  get sourcesTotal(): number { return this.data?.meta?.sourcesQueried ?? 0; }
  get opportunities(): any[] { return (this.data?.opportunities || []).slice(0, 3); }
  get criticalChanges(): number { return this.data?.delta?.summary?.criticalChanges ?? 0; }

  directionColor(): string {
    const d = this.direction;
    if (d === 'risk-off') return 'warn';
    if (d === 'risk-on') return 'primary';
    return 'accent';
  }

  tierColor(tier: string): string {
    if (tier === 'HIGH') return '#f44336';
    if (tier === 'MEDIUM') return '#ff9800';
    return '#8bc34a';
  }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }
}
