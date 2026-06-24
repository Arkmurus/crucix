import { Component, OnInit, OnDestroy } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { Subscription } from 'rxjs';
import { WaConnectionsService, WaAccount } from '../../services/wa-connections.service';

/**
 * WhatsApp Connections management page.
 *
 * Displays all linked WhatsApp accounts, their connection status, and QR codes
 * for pairing new devices. Polls the WA listener every 15s for live status.
 *
 * Robustness design:
 *  - Polling with graceful degradation when aria-wa is unreachable
 *  - QR codes displayed in a modal with auto-refresh every 30s
 *  - Create/delete operations show loading state and error feedback
 *  - Status badges with colour coding for each connection state
 *  - Empty state when no accounts exist
 *  - Error state when the WA listener is down
 */
@Component({
  selector: 'app-wa-connections',
  templateUrl: './wa-connections.component.html',
  styleUrls: ['./wa-connections.component.scss'],
})
export class WaConnectionsComponent implements OnInit, OnDestroy {
  accounts: WaAccount[] = [];
  loading = true;
  error: string | null = null;
  busy = false;

  /** The account whose QR code is currently displayed in the modal. */
  qrAccount: WaAccount | null = null;
  qrHtml: string | null = null;
  qrLoading = false;
  qrError: string | null = null;

  /** Name for a new account being created. */
  newAccountName = '';

  /** Confirmation dialog state for delete. */
  deleteConfirmId: string | null = null;

  private subs: Subscription[] = [];

  constructor(
    private wa: WaConnectionsService,
    private dialog: MatDialog,
  ) {}

  ngOnInit(): void {
    // Subscribe to the reactive account list from the service
    this.subs.push(
      this.wa.accounts$.subscribe(accounts => {
        this.accounts = accounts;
        this.loading = false;
      }),
    );

    // Subscribe to error state
    this.subs.push(
      this.wa.error$.subscribe(err => {
        this.error = err;
      }),
    );

    // Subscribe to busy state
    this.subs.push(
      this.wa.busy$.subscribe(b => {
        this.busy = b;
      }),
    );

    // Start polling
    this.wa.startPolling();
  }

  ngOnDestroy(): void {
    this.wa.stopPolling();
    this.subs.forEach(s => s.unsubscribe());
  }

  // ── Actions ─────────────────────────────────────────────────────────────────

  /** Create a new WhatsApp account. */
  createAccount(): void {
    const name = this.newAccountName.trim();
    if (!name) return;
    this.newAccountName = '';
    this.wa.createAccount(name).subscribe(res => {
      if (res) {
        // If the account has a QR code, show it immediately
        if (res.qr_html) {
          this.qrAccount = res.account;
          this.qrHtml = res.qr_html;
        }
        this.wa.refresh();
      }
    });
  }

  /** Show QR code for an account in a modal. */
  showQr(account: WaAccount): void {
    this.qrAccount = account;
    this.qrHtml = null;
    this.qrLoading = true;
    this.qrError = null;

    this.wa.getAccount(account.id).subscribe(detail => {
      this.qrLoading = false;
      if (detail?.qr_html) {
        this.qrHtml = detail.qr_html;
      } else if (detail?.qr) {
        // Fallback: render the raw QR string as a QR code image
        this.qrHtml = null; // Will show the raw QR text as fallback
        this.qrError = 'QR HTML not available — scan from the raw code below';
      } else {
        this.qrError = detail?.account?.status === 'connected'
          ? 'Already connected — no QR code needed'
          : 'No QR code available yet. Try again in a few seconds.';
      }
    });
  }

  /** Close the QR modal. */
  closeQr(): void {
    this.qrAccount = null;
    this.qrHtml = null;
    this.qrError = null;
  }

  /** Initiate delete confirmation. */
  confirmDelete(id: string): void {
    this.deleteConfirmId = id;
  }

  /** Cancel delete confirmation. */
  cancelDelete(): void {
    this.deleteConfirmId = null;
  }

  /** Execute account deletion. */
  deleteAccount(id: string): void {
    this.deleteConfirmId = null;
    this.wa.deleteAccount(id).subscribe(success => {
      if (success) {
        this.wa.refresh();
      }
    });
  }

  /** Manual refresh. */
  refresh(): void {
    this.loading = true;
    this.wa.refresh();
  }

  // ── Helpers ─────────────────────────────────────────────────────────────────

  /** Human-readable status label. */
  statusLabel(status: string): string {
    const labels: Record<string, string> = {
      connecting: 'Connecting…',
      qr_ready: 'QR Ready — Scan to link',
      connected: 'Connected',
      disconnected: 'Disconnected',
      logged_out: 'Logged Out',
    };
    return labels[status] || status;
  }

  /** CSS class for the status badge. */
  statusClass(status: string): string {
    const classes: Record<string, string> = {
      connecting: 'status-connecting',
      qr_ready: 'status-qr',
      connected: 'status-connected',
      disconnected: 'status-disconnected',
      logged_out: 'status-logged-out',
    };
    return classes[status] || 'status-unknown';
  }

  /** Material icon name for the status. */
  statusIcon(status: string): string {
    const icons: Record<string, string> = {
      connecting: 'hourglass_top',
      qr_ready: 'qr_code_scanner',
      connected: 'check_circle',
      disconnected: 'link_off',
      logged_out: 'logout',
    };
    return icons[status] || 'help_outline';
  }

  /** Whether the account can show a QR code. */
  canShowQr(account: WaAccount): boolean {
    return account.status === 'qr_ready' || account.status === 'connecting';
  }

  /** Format a timestamp for display. */
  formatTime(ts: number | string | null | undefined): string {
    if (!ts) return '—';
    const d = typeof ts === 'number' ? new Date(ts) : new Date(ts);
    return d.toLocaleString();
  }
}
