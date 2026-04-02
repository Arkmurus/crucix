import { Component, OnInit, OnDestroy } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { CrucixApiService } from '../../services/crucix-api.service';

@Component({
  selector: 'app-search',
  templateUrl: './search.component.html',
})
export class SearchComponent implements OnInit, OnDestroy {
  query      = '';
  loading    = false;
  data: any  = null;
  error      = '';
  newSearch  = '';

  private sub: Subscription | null = null;

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private api: CrucixApiService
  ) {}

  ngOnInit(): void {
    this.sub = this.route.queryParams.subscribe(params => {
      const q = params['q'] || '';
      if (q && q !== this.query) {
        this.query    = q;
        this.newSearch = q;
        this.runSearch(q);
      }
    });
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
  }

  runSearch(q: string): void {
    this.loading = true;
    this.data    = null;
    this.error   = '';
    this.api.entitySearch(q).subscribe({
      next: (res: any) => {
        this.loading = false;
        if (res?.success === false) {
          this.error = res.error || 'Search failed';
        } else {
          this.data = res;
        }
      },
      error: () => {
        this.loading = false;
        this.error   = 'Search failed — please try again';
      }
    });
  }

  onSearch(): void {
    const q = this.newSearch.trim();
    if (!q) return;
    this.router.navigate(['/dashboard/search'], { queryParams: { q } });
  }

  confidenceColor(conf: number): string {
    if (conf >= 0.85) return '#10b981';
    if (conf >= 0.70) return '#f59e0b';
    return '#ef4444';
  }

  confidenceLabel(conf: number): string {
    if (conf >= 0.90) return 'High confidence';
    if (conf >= 0.80) return 'Verified';
    if (conf >= 0.70) return 'Partial';
    return 'Low confidence';
  }

  riskColor(sanctioned: boolean): string {
    return sanctioned ? '#ef4444' : '#10b981';
  }

  sanctionLabel(s: any): string {
    if (!s?.checked) return 'Not checked';
    if (s.sanctioned) return 'SANCTIONED';
    return 'Clear';
  }

  formatDate(d: string | null): string {
    if (!d) return 'N/A';
    try { return new Date(d).toLocaleDateString('en-GB', { day:'numeric', month:'short', year:'numeric' }); }
    catch { return d; }
  }

  jurisdictionLabel(code: string): string {
    const map: Record<string,string> = {
      'za':'South Africa','gb':'United Kingdom','us':'United States','pt':'Portugal',
      'ao':'Angola','mz':'Mozambique','ng':'Nigeria','ke':'Kenya','fr':'France',
      'de':'Germany','ae':'UAE','sg':'Singapore','br':'Brazil',
    };
    return map[code?.toLowerCase()] || (code || 'Unknown').toUpperCase();
  }

  listName(id: string): string {
    const map: Record<string,string> = {
      'us_ofac_sdn':'OFAC SDN (US)', 'eu_fsf':'EU Financial Sanctions',
      'un_sc_sanctions':'UN Security Council', 'gb_hmt_sanctions':'UK HMT Sanctions',
      'opensanctions':'OpenSanctions Consolidated',
    };
    return map[id] || id;
  }
}
