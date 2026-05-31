import { Component, OnInit } from '@angular/core';
import { CrucixApiService } from '../../services/crucix-api.service';

@Component({
  selector: 'app-vault',
  templateUrl: './vault.component.html',
  styleUrls: ['./vault.component.scss']
})
export class VaultComponent implements OnInit {
  entries: any[] = [];
  stats: any = {};
  loading = true;
  error = '';

  // Filters
  filterStatus = '';
  filterAgent = '';
  filterSearch = '';
  sortBy = 'updated_at';
  sortDir = 'desc';

  displayedColumns = ['site_name', 'site_url', 'agent_id', 'status', 'created_at', 'updated_at', 'last_verified_at'];

  // Status config
  statusConfig: { [key: string]: { icon: string; color: string } } = {
    pending:   { icon: 'hourglass_empty', color: '#ff9800' },
    registered:{ icon: 'check_circle',    color: '#2196f3' },
    verified:  { icon: 'verified',        color: '#4caf50' },
    failed:    { icon: 'error',           color: '#f44336' },
    expired:   { icon: 'schedule',        color: '#9e9e9e' },
    cancelled: { icon: 'cancel',          color: '#607d8b' },
  };

  constructor(private api: CrucixApiService) {}

  ngOnInit(): void {
    this.loadVault();
  }

  loadVault(): void {
    this.loading = true;
    this.error = '';
    this.api.getVault(this.filterStatus, this.filterAgent, this.filterSearch, this.sortBy, this.sortDir)
      .subscribe(res => {
        if (res?.success) {
          this.entries = res.entries || [];
          this.stats = res.stats || {};
        } else {
          this.error = res?.error || 'Failed to load vault';
          this.entries = [];
        }
        this.loading = false;
      });
  }

  applyFilter(): void {
    this.loadVault();
  }

  clearFilters(): void {
    this.filterStatus = '';
    this.filterAgent = '';
    this.filterSearch = '';
    this.sortBy = 'updated_at';
    this.sortDir = 'desc';
    this.loadVault();
  }

  getStatusIcon(status: string): string {
    return this.statusConfig[status]?.icon || 'help_outline';
  }

  getStatusColor(status: string): string {
    return this.statusConfig[status]?.color || '#9e9e9e';
  }

  get totalSites(): number { return this.stats?.total || 0; }
  get registeredCount(): number { return this.stats?.by_status?.registered || 0; }
  get verifiedCount(): number { return this.stats?.by_status?.verified || 0; }
  get pendingCount(): number { return this.stats?.by_status?.pending || 0; }
  get failedCount(): number { return this.stats?.by_status?.failed || 0; }
  get staleCount(): number { return this.stats?.stale_unverified || 0; }

  formatDate(ts: number | null): string {
    if (!ts) return '--';
    const d = new Date(ts * 1000);
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  copyUrl(url: string): void {
    navigator.clipboard.writeText(url).catch(() => {});
  }
}
