import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { environment } from '../../environments/environment';

@Injectable({ providedIn: 'root' })
export class CrucixApiService {
  private base = environment.apiBase;

  // Basic auth header — matches Express dashboard credentials
  private get headers(): HttpHeaders {
    const user = 'arkmurus';
    const pass = 'Crucix2026!';
    return new HttpHeaders({
      'Authorization': 'Basic ' + btoa(`${user}:${pass}`)
    });
  }

  constructor(private http: HttpClient) {}

  // ── Core intelligence data ──────────────────────────────────────────────────

  getData(): Observable<any> {
    return this.http.get(`${this.base}/api/data`, { headers: this.headers })
      .pipe(catchError(() => of(null)));
  }

  getHealth(): Observable<any> {
    return this.http.get(`${this.base}/api/health`)
      .pipe(catchError(() => of(null)));
  }

  getSourceHealth(): Observable<any> {
    return this.http.get(`${this.base}/api/source-health`, { headers: this.headers })
      .pipe(catchError(() => of({ sources: [], degraded: [], healthyCount: 0, degradedCount: 0 })));
  }

  // ── Self-learning endpoints ─────────────────────────────────────────────────

  getOpportunities(): Observable<any> {
    return this.http.get(`${this.base}/api/opportunities`, { headers: this.headers })
      .pipe(catchError(() => of({ opportunities: [] })));
  }

  getPatterns(): Observable<any> {
    return this.http.get(`${this.base}/api/patterns`, { headers: this.headers })
      .pipe(catchError(() => of({ patterns: [] })));
  }

  getLearningStats(): Observable<any> {
    return this.http.get(`${this.base}/api/learning/stats`, { headers: this.headers })
      .pipe(catchError(() => of(null)));
  }

  getLearningOutcomes(): Observable<any> {
    return this.http.get(`${this.base}/api/learning/outcomes`, { headers: this.headers })
      .pipe(catchError(() => of([])));
  }

  recordOutcome(hash: string, text: string, outcome: 'confirmed' | 'dismissed'): Observable<any> {
    return this.http.post(`${this.base}/api/learning/outcome`,
      { hash, text, outcome },
      { headers: this.headers }
    ).pipe(catchError(() => of({ success: false })));
  }

  getExplorerFindings(): Observable<any> {
    return this.http.get(`${this.base}/api/explorer`, { headers: this.headers })
      .pipe(catchError(() => of({ findings: [] })));
  }

  runExploration(): Observable<any> {
    return this.http.post(`${this.base}/api/explorer/run`, {}, { headers: this.headers })
      .pipe(catchError(() => of({ success: false })));
  }

  // ── Self-update endpoints ───────────────────────────────────────────────────

  getStagedModules(): Observable<any> {
    return this.http.get(`${this.base}/api/self/staged`, { headers: this.headers })
      .pipe(catchError(() => of({ staged: [] })));
  }

  generateModule(description: string, moduleName: string): Observable<any> {
    return this.http.post(`${this.base}/api/self/generate`,
      { description, moduleName },
      { headers: this.headers }
    ).pipe(catchError(err => of({ success: false, error: err.message })));
  }

  applyModule(moduleName: string): Observable<any> {
    return this.http.post(`${this.base}/api/self/apply`,
      { moduleName },
      { headers: this.headers }
    ).pipe(catchError(err => of({ success: false, error: err.message })));
  }

  getUpdateLog(): Observable<any> {
    return this.http.get(`${this.base}/api/self/update-log`, { headers: this.headers })
      .pipe(catchError(() => of({ log: [] })));
  }

  // ── Actions ─────────────────────────────────────────────────────────────────

  triggerSweep(): Observable<any> {
    return this.http.post(`${this.base}/api/sweep`, {}, { headers: this.headers })
      .pipe(catchError(err => of({ success: false, error: err.message })));
  }

  search(query: string): Observable<any> {
    return this.http.get(`${this.base}/api/search?q=${encodeURIComponent(query)}`)
      .pipe(catchError(() => of({ results: [] })));
  }
}
