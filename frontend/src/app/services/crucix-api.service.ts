import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { environment } from '../../environments/environment';

// JWT Bearer token is added automatically by AuthInterceptor for all requests.
// No manual Authorization headers needed here.

@Injectable({ providedIn: 'root' })
export class CrucixApiService {
  private base = environment.apiBase;

  constructor(private http: HttpClient) {}

  // ── Core intelligence data ──────────────────────────────────────────────────

  getData(): Observable<any> {
    return this.http.get(`${this.base}/api/data`)
      .pipe(catchError(() => of(null)));
  }

  getHealth(): Observable<any> {
    return this.http.get(`${this.base}/api/health`)
      .pipe(catchError(() => of(null)));
  }

  getSourceHealth(): Observable<any> {
    return this.http.get(`${this.base}/api/source-health`)
      .pipe(catchError(() => of({ sources: [], degraded: [], healthyCount: 0, degradedCount: 0 })));
  }

  // ── Self-learning endpoints ─────────────────────────────────────────────────

  getOpportunities(): Observable<any> {
    return this.http.get(`${this.base}/api/opportunities`)
      .pipe(catchError(() => of({ opportunities: [] })));
  }

  getPatterns(): Observable<any> {
    return this.http.get(`${this.base}/api/patterns`)
      .pipe(catchError(() => of({ patterns: [] })));
  }

  getLearningStats(): Observable<any> {
    return this.http.get(`${this.base}/api/learning/stats`)
      .pipe(catchError(() => of(null)));
  }

  getLearningOutcomes(): Observable<any> {
    return this.http.get(`${this.base}/api/learning/outcomes`)
      .pipe(catchError(() => of([])));
  }

  recordOutcome(hash: string, text: string, outcome: 'confirmed' | 'dismissed'): Observable<any> {
    return this.http.post(`${this.base}/api/learning/outcome`, { hash, text, outcome })
      .pipe(catchError(() => of({ success: false })));
  }

  getBDIntelligence(): Observable<any> {
    return this.http.get(`${this.base}/api/bd-intelligence`)
      .pipe(catchError(() => of({ tenders: [], ideas: [], strategy: null, pipeline: [], counts: {} })));
  }

  updateDealStage(dealId: string, stage: string, notes?: string): Observable<any> {
    return this.http.post(`${this.base}/api/bd-intelligence/pipeline/${dealId}/stage`, { stage, notes })
      .pipe(catchError(err => of({ ok: false, error: err.message })));
  }

  getExplorerFindings(): Observable<any> {
    return this.http.get(`${this.base}/api/explorer`)
      .pipe(catchError(() => of({ findings: [] })));
  }

  runExploration(): Observable<any> {
    return this.http.post(`${this.base}/api/explorer/run`, {})
      .pipe(catchError(() => of({ success: false })));
  }

  // ── Self-update endpoints ───────────────────────────────────────────────────

  getStagedModules(): Observable<any> {
    return this.http.get(`${this.base}/api/self/staged`)
      .pipe(catchError(() => of({ staged: [] })));
  }

  generateModule(description: string, moduleName: string): Observable<any> {
    return this.http.post(`${this.base}/api/self/generate`, { description, moduleName })
      .pipe(catchError(err => of({ success: false, error: err.message })));
  }

  applyModule(moduleName: string): Observable<any> {
    return this.http.post(`${this.base}/api/self/apply`, { moduleName })
      .pipe(catchError(err => of({ success: false, error: err.message })));
  }

  getUpdateLog(): Observable<any> {
    return this.http.get(`${this.base}/api/self/update-log`)
      .pipe(catchError(() => of({ log: [] })));
  }

  // ── Actions ─────────────────────────────────────────────────────────────────

  triggerSweep(): Observable<any> {
    return this.http.post(`${this.base}/api/sweep`, {})
      .pipe(catchError(err => of({ success: false, error: err.message })));
  }

  search(query: string): Observable<any> {
    return this.http.get(`${this.base}/api/search?q=${encodeURIComponent(query)}`)
      .pipe(catchError(() => of({ results: [] })));
  }
}
