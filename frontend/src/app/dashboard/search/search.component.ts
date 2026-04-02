import { Component, OnInit, OnDestroy } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { AuthService } from '../../services/auth.service';

interface Phase { name: string; detail: string; icon: string; status: 'running' | 'done'; }

@Component({
  selector: 'app-search',
  templateUrl: './search.component.html',
})
export class SearchComponent implements OnInit, OnDestroy {
  query     = '';
  newSearch = '';
  loading   = false;
  error     = '';

  // Live streaming state
  phases:       Phase[]  = [];
  companies:    any[]    = [];
  officers:     any[]    = [];
  psc:          any[]    = [];
  appointments: any[]    = [];
  gleif:        any      = null;
  sanctions:    any      = null;
  wikidata:     any[]    = [];
  icij:         any[]    = [];
  openOwnership:any[]    = [];
  news:         any[]    = [];
  adverseMedia: any[]    = [];
  web:          any[]    = [];
  intel:        any[]    = [];
  synthesis:    any      = null;
  networkGraph: any      = null;
  alerts:       any[]    = [];
  followUps:    any[]    = [];
  finalResult:  any      = null;

  private routeSub: Subscription | null = null;
  private eventSource: EventSource | null = null;

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private auth: AuthService
  ) {}

  ngOnInit(): void {
    this.routeSub = this.route.queryParams.subscribe(params => {
      const q = params['q'] || '';
      if (q && q !== this.query) {
        this.query     = q;
        this.newSearch = q;
        this.startDeepSearch(q);
      }
    });
  }

  ngOnDestroy(): void {
    this.routeSub?.unsubscribe();
    this.eventSource?.close();
  }

  startDeepSearch(q: string): void {
    this.eventSource?.close();
    this.resetState();
    this.loading = true;
    this.error   = '';

    const token = this.auth.getToken();
    const url   = `/api/search/deep?q=${encodeURIComponent(q)}` +
                  (token ? `&token=${encodeURIComponent(token)}` : '');

    this.eventSource = new EventSource(url);

    this.eventSource.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        this.handleEvent(msg);
      } catch {}
    };

    this.eventSource.onerror = () => {
      this.loading = false;
      this.eventSource?.close();
      if (!this.finalResult) this.error = 'Search stream interrupted. Please try again.';
    };
  }

  handleEvent(msg: any): void {
    switch (msg.type) {
      case 'phase':
        if (this.phases.length) this.phases[this.phases.length - 1].status = 'done';
        this.phases.push({ name: msg.name, detail: msg.detail, icon: msg.icon || '⚙️', status: 'running' });
        break;

      case 'finding':
        switch (msg.category) {
          case 'companies':     this.companies.push(...(msg.items || [])); break;
          case 'officers':      this.officers.push(...(msg.items || [])); break;
          case 'psc':           this.psc.push(...(msg.items || [])); break;
          case 'charges':       break; // handled in finalResult
          case 'appointments':  this.appointments.push(msg); break;
          case 'gleif':         this.gleif = msg.data; break;
          case 'wikidata':      this.wikidata = [msg.data]; break;
          case 'icij':          this.icij.push(...(msg.items || [])); break;
          case 'openOwnership': this.openOwnership.push(...(msg.items || [])); break;
          case 'news':          this.news.push(...(msg.items || [])); break;
          case 'adverseMedia':  this.adverseMedia.push(...(msg.items || [])); break;
          case 'intel':         this.intel.push(...(msg.items || [])); break;
          case 'followUp':      this.followUps.push(msg); this.news.push(...(msg.items || [])); break;
        }
        break;

      case 'alert':
        this.alerts.push(msg);
        break;

      case 'synthesis':
        this.synthesis = msg.data;
        break;

      case 'result':
        this.finalResult = msg.data;
        if (this.phases.length) this.phases[this.phases.length - 1].status = 'done';
        // Merge any final data not yet streamed
        if (msg.data) {
          if (!this.gleif && msg.data.gleif)            this.gleif = msg.data.gleif;
          if (!this.sanctions && msg.data.sanctions)    this.sanctions = msg.data.sanctions;
          if (!this.networkGraph && msg.data.networkGraph) this.networkGraph = msg.data.networkGraph;
          if (!this.synthesis && msg.data.synthesis)   this.synthesis = msg.data.synthesis;
        }
        this.loading = false;
        this.eventSource?.close();
        break;

      case 'complete':
        this.loading = false;
        break;

      case 'error':
        this.error   = msg.message || 'Search failed';
        this.loading = false;
        this.eventSource?.close();
        break;
    }
  }

  resetState(): void {
    this.phases = []; this.companies = []; this.officers = []; this.psc = [];
    this.appointments = []; this.gleif = null; this.sanctions = null;
    this.wikidata = []; this.icij = []; this.openOwnership = [];
    this.news = []; this.adverseMedia = []; this.web = []; this.intel = [];
    this.synthesis = null; this.networkGraph = null; this.alerts = [];
    this.followUps = []; this.finalResult = null;
  }

  onSearch(): void {
    const q = this.newSearch.trim();
    if (!q) return;
    this.router.navigate(['/dashboard/search'], { queryParams: { q } });
  }

  get hasResults(): boolean {
    return this.companies.length > 0 || this.officers.length > 0 || this.psc.length > 0 ||
           !!this.gleif || !!this.synthesis || this.news.length > 0 || this.icij.length > 0;
  }

  get riskColor(): string {
    const lvl = this.synthesis?.riskLevel || (this.sanctions?.sanctioned ? 'critical' : 'low');
    if (lvl === 'critical') return '#ef4444';
    if (lvl === 'high')     return '#f59e0b';
    if (lvl === 'medium')   return '#3b82f6';
    return '#10b981';
  }

  get overallConfidence(): number {
    return this.finalResult?.confidence || 0;
  }

  confidenceColor(c: number): string {
    if (c >= 0.85) return '#10b981';
    if (c >= 0.70) return '#f59e0b';
    return '#ef4444';
  }

  formatDate(d: string | null): string {
    if (!d) return 'N/A';
    try { return new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }); }
    catch { return d; }
  }

  jurisdictionLabel(code: string): string {
    const map: Record<string, string> = {
      'gb':'United Kingdom','us':'United States','za':'South Africa','pt':'Portugal',
      'ao':'Angola','mz':'Mozambique','ng':'Nigeria','ke':'Kenya','fr':'France',
      'de':'Germany','ae':'UAE','sg':'Singapore','nl':'Netherlands','ch':'Switzerland',
      'cy':'Cyprus','bvi':'British Virgin Islands','ky':'Cayman Islands',
    };
    return map[code?.toLowerCase()] || (code || '').toUpperCase();
  }

  listName(id: string): string {
    const m: Record<string,string> = {
      'us_ofac_sdn':'OFAC SDN (US)', 'eu_fsf':'EU Financial Sanctions',
      'un_sc_sanctions':'UN Security Council', 'gb_hmt_sanctions':'UK HMT',
    };
    return m[id] || id;
  }

  dbName(id: string): string {
    const m: Record<string,string> = {
      'panama_papers':'Panama Papers','pandora_papers':'Pandora Papers',
      'paradise_papers':'Paradise Papers','bahamas_leaks':'Bahamas Leaks',
    };
    return m[id] || id;
  }
}
