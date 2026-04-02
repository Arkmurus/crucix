import { Component, OnInit } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { AuthService, User } from '../../services/auth.service';
import { CrucixApiService } from '../../services/crucix-api.service';

@Component({
  selector: 'app-admin-users',
  templateUrl: './users.component.html',
  styleUrls: ['./users.component.scss']
})
export class UsersComponent implements OnInit {
  users: User[] = [];
  loading = true;
  error = '';
  successMsg = '';
  saving = new Set<string>();

  showAudit = false;
  auditLog: any[] = [];
  auditLoading = false;

  constructor(
    private http: HttpClient,
    private auth: AuthService,
    private api: CrucixApiService
  ) {}

  ngOnInit(): void { this.loadUsers(); }

  get pendingUsers(): User[] {
    return this.users.filter(u => u.status === 'pending_approval');
  }

  private headers(): HttpHeaders {
    return new HttpHeaders({ Authorization: `Bearer ${this.auth.getToken()}` });
  }

  loadUsers(): void {
    this.loading = true;
    this.error = '';
    this.http.get<User[]>('/api/admin/users', { headers: this.headers() }).subscribe({
      next: users => { this.users = users; this.loading = false; },
      error: err  => { this.error = err.error?.error || 'Failed to load users'; this.loading = false; }
    });
  }

  updateUser(user: User, changes: Partial<User>): void {
    this.saving.add(user.id);
    this.successMsg = '';
    this.http.put<User>(`/api/admin/users/${user.id}`, changes, { headers: this.headers() }).subscribe({
      next: updated => {
        const idx = this.users.findIndex(u => u.id === user.id);
        if (idx !== -1) this.users[idx] = updated;
        this.saving.delete(user.id);
        this.successMsg = `${updated.username} updated.`;
        setTimeout(() => this.successMsg = '', 3000);
      },
      error: err => {
        this.error = err.error?.error || 'Update failed';
        this.saving.delete(user.id);
      }
    });
  }

  approveUser(user: User): void {
    this.updateUser(user, { status: 'active' } as any);
  }

  rejectUser(user: User): void {
    if (!confirm(`Reject "${user.username}"? They will receive a rejection email and be removed.`)) return;
    this.saving.add(user.id);
    this.http.delete(`/api/admin/users/${user.id}`, { headers: this.headers() }).subscribe({
      next: () => {
        this.users = this.users.filter(u => u.id !== user.id);
        this.saving.delete(user.id);
        this.successMsg = `${user.username} rejected and notified.`;
        setTimeout(() => this.successMsg = '', 3000);
      },
      error: err => {
        this.error = err.error?.error || 'Reject failed';
        this.saving.delete(user.id);
      }
    });
  }

  setRole(user: User, role: string): void {
    this.updateUser(user, { role: role as any });
  }

  toggleStatus(user: User): void {
    const newStatus = user.status === 'suspended' ? 'active' : 'suspended';
    this.updateUser(user, { status: newStatus } as any);
  }

  forceLogout(user: User): void {
    if (!confirm(`Force logout "${user.username}"? Their active session will be invalidated immediately.`)) return;
    this.saving.add(user.id);
    this.api.forceLogout(user.id).subscribe({
      next: (res: any) => {
        this.saving.delete(user.id);
        if (res.ok) {
          this.successMsg = `${user.username}'s session revoked.`;
        } else {
          this.error = res.error || 'Force logout failed';
        }
        setTimeout(() => this.successMsg = '', 3000);
      },
      error: () => { this.saving.delete(user.id); this.error = 'Force logout failed'; }
    });
  }

  deleteUser(user: User): void {
    if (!confirm(`Permanently delete "${user.username}"? This cannot be undone.`)) return;
    this.saving.add(user.id);
    this.http.delete(`/api/admin/users/${user.id}`, { headers: this.headers() }).subscribe({
      next: () => {
        this.users = this.users.filter(u => u.id !== user.id);
        this.saving.delete(user.id);
        this.successMsg = 'User deleted.';
        setTimeout(() => this.successMsg = '', 3000);
      },
      error: err => {
        this.error = err.error?.error || 'Delete failed';
        this.saving.delete(user.id);
      }
    });
  }

  toggleAuditLog(): void {
    this.showAudit = !this.showAudit;
    if (this.showAudit && this.auditLog.length === 0) {
      this.auditLoading = true;
      this.api.getAuditLog().subscribe(log => {
        this.auditLog = log;
        this.auditLoading = false;
      });
    }
  }

  auditIcon(action: string): string {
    const icons: Record<string, string> = {
      approve:      'how_to_reg',
      reject:       'person_off',
      suspend:      'pause_circle',
      unsuspend:    'play_circle',
      delete:       'delete_outline',
      force_logout: 'logout',
      role_change:  'manage_accounts',
    };
    return icons[action] || 'history';
  }

  auditColor(action: string): string {
    if (action === 'approve' || action === 'unsuspend') return '#a5d6a7';
    if (action === 'reject'  || action === 'delete')   return '#ef9a9a';
    if (action === 'suspend' || action === 'force_logout') return '#ffcc80';
    return '#90caf9';
  }

  auditLabel(action: string): string {
    const labels: Record<string, string> = {
      approve:      'Approved',
      reject:       'Rejected',
      suspend:      'Suspended',
      unsuspend:    'Reactivated',
      delete:       'Deleted',
      force_logout: 'Force Logout',
      role_change:  'Role Changed',
    };
    return labels[action] || action;
  }

  statusLabel(status: string): string {
    if (status === 'pending_approval')    return 'Pending Approval';
    if (status === 'pending_verification') return 'Verify Email';
    if (status === 'active')   return 'Active';
    if (status === 'suspended') return 'Suspended';
    return status;
  }
}
