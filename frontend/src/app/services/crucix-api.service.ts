import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of, Subject } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { environment } from '../../environments/environment';

export interface AriaChatEvent {
  type: 'status' | 'chunk' | 'done' | 'error' | 'meta';
  text?: string;
  message?: string;
  session_id?: string;
  model?: string;
  tool_used?: string;
  trivial?: boolean;
  degraded?: boolean;
  [key: string]: any;
}

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

  forceLogout(userId: string): Observable<any> {
    return this.http.post(`${this.base}/api/admin/users/${userId}/force-logout`, {})
      .pipe(catchError(err => of({ ok: false, error: err.error?.error || 'Failed' })));
  }

  getAuditLog(): Observable<any[]> {
    return this.http.get<any[]>(`${this.base}/api/admin/audit`)
      .pipe(catchError(() => of([])));
  }

  updateDealStage(dealId: string, stage: string, notes?: string): Observable<any> {
    return this.http.post(`${this.base}/api/bd-intelligence/pipeline/${dealId}/stage`, { stage, notes })
      .pipe(catchError(err => of({ ok: false, error: err.message })));
  }

  recordDealOutcome(dealId: string, market: string, type: string, outcome: 'WON' | 'LOST' | 'NO_BID', reason?: string): Observable<any> {
    return this.http.post(`${this.base}/api/bd-intelligence/pipeline/${dealId}/outcome`, { market, type, outcome, reason })
      .pipe(catchError(err => of({ ok: false, error: err.message })));
  }

  sendLeadFeedback(signalText: string, market: string, feedback: 'positive' | 'negative', reason?: string): Observable<any> {
    return this.http.post(`${this.base}/api/bd-intelligence/feedback`, { signalText, market, feedback, reason })
      .pipe(catchError(() => of({ ok: false })));
  }

  screenCompliance(sellerCountry: string, buyerCountry: string, productCategory: string, dealValueUSD?: number): Observable<any> {
    return this.http.post(`${this.base}/api/compliance/screen`, { sellerCountry, buyerCountry, productCategory, dealValueUSD })
      .pipe(catchError(err => of({ error: err.error?.error || 'Screen failed' })));
  }

  getComplianceProducts(): Observable<any[]> {
    return this.http.get<any[]>(`${this.base}/api/compliance/products`)
      .pipe(catchError(() => of([])));
  }

  createShareBrief(): Observable<any> {
    return this.http.post(`${this.base}/api/share/brief`, {})
      .pipe(catchError(err => of({ error: err.error?.error || 'Failed to create share link' })));
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

  // ── Brain ML endpoints ──────────────────────────────────────────────────────

  getBrainLeads(): Observable<any[]> {
    return this.http.get<any[]>(`${this.base}/api/brain/leads`)
      .pipe(catchError(() => of([])));
  }

  getBrainBrief(): Observable<any> {
    return this.http.get(`${this.base}/api/brain/brief`)
      .pipe(catchError(() => of(null)));
  }

  getBrainStatus(): Observable<any> {
    return this.http.get(`${this.base}/api/brain/status`)
      .pipe(catchError(() => of({ last_run: null })));
  }

  getBrainHistory(): Observable<any[]> {
    return this.http.get<any[]>(`${this.base}/api/brain/history`)
      .pipe(catchError(() => of([])));
  }

  // ── ARIA endpoints ──────────────────────────────────────────────────────────

  getAriaIdentity(): Observable<any> {
    return this.http.get(`${this.base}/api/aria/identity`)
      .pipe(catchError(() => of({ name: 'ARIA', status: 'unavailable', age_days: 0 })));
  }

  getAriaThoughts(): Observable<any[]> {
    return this.http.get<any[]>(`${this.base}/api/aria/thoughts`)
      .pipe(catchError(() => of([])));
  }

  getAriaCuriosity(): Observable<any> {
    return this.http.get(`${this.base}/api/aria/curiosity`)
      .pipe(catchError(() => of({ open_threads: [] })));
  }

  ariaChat(message: string, sessionId?: string): Observable<any> {
    return this.http.post(`${this.base}/api/aria/chat`, { message, session_id: sessionId })
      .pipe(catchError(err => of({ response: 'ARIA is unavailable. ' + (err.error?.error || ''), fallback: true })));
  }

  ariaThink(question: string, context?: any, fast = false): Observable<any> {
    return this.http.post(`${this.base}/api/aria/think`, { question, context: context || {}, fast })
      .pipe(catchError(err => of({ error: err.error?.error || 'ARIA brain service not connected' })));
  }

  // ── Streaming ARIA Chat ─────────────────────────────────────────────────────

  private _streamAbort: AbortController | null = null;

  /**
   * Stream a chat message to ARIA via SSE. Returns an Observable that emits
   * AriaChatEvent objects as they arrive from the SSE stream.
   */
  ariaChatStream(message: string, sessionId?: string): Observable<AriaChatEvent> {
    const subject = new Subject<AriaChatEvent>();
    const token = localStorage.getItem('ark_token');

    // Cancel any existing stream
    this.stopStream();
    this._streamAbort = new AbortController();

    const url = `${this.base}/api/aria/chat/stream`;
    const body = JSON.stringify({ message, session_id: sessionId || '' });

    fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
      },
      body,
      signal: this._streamAbort.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          subject.next({ type: 'error', message: `HTTP ${response.status}` });
          subject.complete();
          return;
        }

        const reader = response.body!.getReader();
        const decoder = new TextDecoder();
        let buf = '';

        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buf += decoder.decode(value, { stream: true });
            const lines = buf.split('\n');
            buf = lines.pop() || '';

            for (const line of lines) {
              const trimmed = line.trim();
              if (!trimmed || !trimmed.startsWith('data: ')) continue;
              try {
                const event: AriaChatEvent = JSON.parse(trimmed.slice(6));
                subject.next(event);
              } catch {}
            }
          }
        } catch (e: any) {
          if (e.name !== 'AbortError') {
            subject.next({ type: 'error', message: e.message });
          }
        }

        subject.complete();
      })
      .catch((e: any) => {
        if (e.name !== 'AbortError') {
          subject.next({ type: 'error', message: e.message || 'Stream failed' });
        }
        subject.complete();
      });

    return subject.asObservable();
  }

  stopStream(): void {
    if (this._streamAbort) {
      this._streamAbort.abort();
      this._streamAbort = null;
    }
  }

  // ── Conversation History ────────────────────────────────────────────────────

  getConversations(offset = 0, limit = 30): Observable<any> {
    return this.http.get(`${this.base}/api/aria/conversations?offset=${offset}&limit=${limit}`)
      .pipe(catchError(() => of({ conversations: [] })));
  }

  loadConversation(sessionId: string): Observable<any> {
    return this.http.get(`${this.base}/api/aria/conversations/${sessionId}`)
      .pipe(catchError(() => of(null)));
  }

  deleteConversation(sessionId: string): Observable<any> {
    return this.http.delete(`${this.base}/api/aria/conversations/${sessionId}`)
      .pipe(catchError(() => of({ deleted: false })));
  }

  renameConversation(sessionId: string, title: string): Observable<any> {
    return this.http.put(`${this.base}/api/aria/conversations/${sessionId}/title`, { title })
      .pipe(catchError(() => of({ ok: false })));
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

  entitySearch(query: string): Observable<any> {
    return this.http.get(`${this.base}/api/search/entity?q=${encodeURIComponent(query)}`)
      .pipe(catchError(err => of({ success: false, error: err?.error?.error || 'Search failed' })));
  }
}
