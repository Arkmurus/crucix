import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of, BehaviorSubject, timer, Subject } from 'rxjs';
import { catchError, map, switchMap, tap, retry, shareReplay } from 'rxjs/operators';
import { environment } from '../../environments/environment';

export interface WaAccount {
  id: string;
  name: string;
  status: 'connecting' | 'qr_ready' | 'connected' | 'disconnected' | 'logged_out';
  connected: boolean;
  started_at: string | null;
  created_at: number;
  last_active: number | null;
  has_qr: boolean;
}

export interface WaAccountDetail extends WaAccount {
  qr: string | null;
  qr_html: string | null;
}

export interface WaAccountsResponse {
  accounts: WaAccount[];
  count: number;
}

export interface WaAccountDetailResponse {
  account: WaAccount;
  qr: string | null;
  qr_html: string | null;
}

export interface WaCreateResponse {
  account: WaAccount;
  qr: string | null;
  qr_html: string | null;
}

/**
 * WhatsApp Connections API service.
 * Proxied through the Express backend (server.mjs) which forwards to aria-wa.
 * The AuthInterceptor automatically attaches the JWT Bearer token.
 */
@Injectable({ providedIn: 'root' })
export class WaConnectionsService {
  private base = environment.apiBase;

  /** Emits the current account list on every successful poll. */
  readonly accounts$ = new BehaviorSubject<WaAccount[]>([]);

  /** Emits the last error message, or null when healthy. */
  readonly error$ = new BehaviorSubject<string | null>(null);

  /** Emits true while a create/delete operation is in flight. */
  readonly busy$ = new BehaviorSubject<boolean>(false);

  /** Polling interval in ms (default 15s — QR codes expire in 60s). */
  private pollInterval = 15_000;

  /** Internal poll subscription handle. */
  private pollSub: any = null;

  constructor(private http: HttpClient) {}

  // ── Public API ──────────────────────────────────────────────────────────────

  /** Fetch all WhatsApp accounts. */
  getAccounts(): Observable<WaAccountsResponse> {
    return this.http.get<WaAccountsResponse>(`${this.base}/api/wa-listener/accounts`).pipe(
      catchError(err => {
        const msg = err.status === 0
          ? 'WA listener unreachable — check that aria-wa is running'
          : err.error?.error || `Failed to fetch accounts (${err.status})`;
        return of({ accounts: [], count: 0, _error: msg } as any);
      }),
    );
  }

  /** Fetch a single account with its QR code. */
  getAccount(id: string): Observable<WaAccountDetailResponse | null> {
    return this.http.get<WaAccountDetailResponse>(`${this.base}/api/wa-listener/accounts/${id}`).pipe(
      catchError(err => {
        this.error$.next(err.error?.error || `Failed to fetch account ${id}`);
        return of(null);
      }),
    );
  }

  /** Create a new WhatsApp account (returns QR code). */
  createAccount(name: string): Observable<WaCreateResponse | null> {
    this.busy$.next(true);
    return this.http.post<WaCreateResponse>(`${this.base}/api/wa-listener/accounts`, { name }).pipe(
      tap({
        next: () => this.busy$.next(false),
        error: () => this.busy$.next(false),
      }),
      catchError(err => {
        this.error$.next(err.error?.error || 'Failed to create account');
        this.busy$.next(false);
        return of(null);
      }),
    );
  }

  /** Delete a WhatsApp account. */
  deleteAccount(id: string): Observable<boolean> {
    this.busy$.next(true);
    return this.http.delete(`${this.base}/api/wa-listener/accounts/${id}`).pipe(
      map(() => true),
      tap({
        next: () => this.busy$.next(false),
        error: () => this.busy$.next(false),
      }),
      catchError(err => {
        this.error$.next(err.error?.error || 'Failed to delete account');
        this.busy$.next(false);
        return of(false);
      }),
    );
  }

  /** Get the QR code HTML page URL (for iframe embedding). */
  getQrPageUrl(id: string): string {
    return `${this.base}/api/wa-listener/accounts/${id}/qr`;
  }

  // ── Polling ─────────────────────────────────────────────────────────────────

  /** Start polling for account list updates. */
  startPolling(): void {
    if (this.pollSub) return;
    this.pollSub = timer(0, this.pollInterval).pipe(
      switchMap(() => this.getAccounts()),
      tap(res => {
        const accounts = (res as any).accounts || [];
        this.accounts$.next(accounts);
        this.error$.next(null);
      }),
      catchError(err => {
        this.error$.next(err?.message || 'Poll failed');
        return of(null);
      }),
    ).subscribe();
  }

  /** Stop polling. */
  stopPolling(): void {
    if (this.pollSub) {
      this.pollSub.unsubscribe();
      this.pollSub = null;
    }
  }

  /** Manually trigger a refresh. */
  refresh(): void {
    this.getAccounts().subscribe(res => {
      this.accounts$.next((res as any).accounts || []);
    });
  }
}
