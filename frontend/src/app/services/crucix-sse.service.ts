import { Injectable, OnDestroy } from '@angular/core';
import { Subject } from 'rxjs';
import { environment } from '../../environments/environment';

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

  readonly events$ = this._events$.asObservable();

  get connected(): boolean { return this._connected; }

  connect(): void {
    if (this.eventSource) return; // already connected

    const url = `${environment.apiBase}${environment.sseUrl}`;
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
