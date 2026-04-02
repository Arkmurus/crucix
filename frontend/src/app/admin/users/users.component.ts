import { Component, OnInit } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { AuthService, User } from '../../services/auth.service';

@Component({
  selector: 'app-admin-users',
  templateUrl: './users.component.html'
})
export class UsersComponent implements OnInit {
  users: User[] = [];
  loading = true;
  error = '';
  successMsg = '';
  saving = new Set<string>();

  constructor(private http: HttpClient, private auth: AuthService) {}

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
    this.updateUser(user, { status: 'active' });
  }

  setRole(user: User, role: string): void {
    this.updateUser(user, { role: role as any });
  }

  toggleStatus(user: User): void {
    const newStatus = user.status === 'suspended' ? 'active' : 'suspended';
    this.updateUser(user, { status: newStatus });
  }

  deleteUser(user: User): void {
    if (!confirm(`Delete user "${user.username}"? This cannot be undone.`)) return;
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

  statusBadge(status: string): string {
    if (status === 'active') return 'badge bg-success';
    if (status === 'pending_approval') return 'badge bg-warning text-dark';
    if (status === 'pending_verification') return 'badge bg-info text-dark';
    if (status === 'suspended') return 'badge bg-danger';
    return 'badge bg-secondary';
  }

  statusLabel(status: string): string {
    if (status === 'pending_approval') return 'Pending Approval';
    if (status === 'pending_verification') return 'Verify Email';
    if (status === 'active') return 'Active';
    if (status === 'suspended') return 'Suspended';
    return status;
  }

  suspendBtnClass(status: string): string {
    return status === 'suspended'
      ? 'btn btn-sm btn-outline-success'
      : 'btn btn-sm btn-outline-warning';
  }
}
