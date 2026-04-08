import { Injectable, OnDestroy } from '@angular/core';
import { Subject } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthService } from './auth.service';

export interface SseEvent {
  type: 'update' | 'sweep_start' | 'sweep_error' | 'connected';
  data?: any;
  timestamp?: string;
}

@Injectable({ providedIn: 'root' })
export class CrucixSseService implements OnDestroy {
  private eventSource: EventSource | null = null;
  private _events$ = new Subject<SseEvent>();
  private _connected = false;

  constructor(private authService: AuthService) {}

  readonly events$ = this._events$.asObservable();

  get connected(): boolean { return this._connected; }

  connect(): void {
    if (this.eventSource) return; // already connected

    // EventSource cannot send custom headers, so the JWT is passed via the
    // ?token= query param. The server's /events handler accepts either the
    // query param or an Authorization header. Without this the SSE stream
    // 401s after the 2026-04-09 server-side auth was added.
    const token = this.authService.getToken();
    const base = `${environment.apiBase}${environment.sseUrl}`;
    const url = token ? `${base}?token=${encodeURIComponent(token)}` : base;
    this.eventSource = new EventSource(url);

    this.eventSource.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        this._connected = true;
        this._events$.next(data as SseEvent);
      } catch {}
    };

    this.eventSource.onerror = () => {
      this._connected = false;
      // EventSource auto-reconnects — no manual action needed
    };

    this.eventSource.onopen = () => {
      this._connected = true;
    };
  }

  disconnect(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
      this._connected = false;
    }
  }

  ngOnDestroy(): void {
    this.disconnect();
  }
}
