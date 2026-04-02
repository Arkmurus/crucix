import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ReactiveFormsModule, FormBuilder, FormGroup, Validators } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { MatModule } from 'src/app/appModules/mat.module';
import { AuthService, User } from 'src/app/services/auth.service';
import { PushService } from 'src/app/services/push.service';

@Component({
  selector: 'app-user-profile',
  standalone: true,
  imports: [CommonModule, MatModule, ReactiveFormsModule],
  templateUrl: './user-profile.component.html',
  styleUrl: './user-profile.component.scss'
})
export class UserProfileComponent implements OnInit {
  currentUser: User | null = null;
  isAdmin = false;

  accountForm!: FormGroup;
  notifyForm!: FormGroup;
  passwordForm!: FormGroup;

  loading = false;
  errorMsg = '';
  successMsg = '';

  pushLoading = false;
  pushSubscribed = false;
  pushSupported = false;

  adminUsers: User[] = [];
  adminLoading = false;
  adminError = '';

  displayedColumns = ['username', 'email', 'role', 'status', 'actions'];

  hideCurrentPw = true;
  hideNewPw = true;
  hideConfirmPw = true;

  constructor(
    private fb: FormBuilder,
    private authService: AuthService,
    private pushService: PushService,
    private http: HttpClient
  ) {}

  ngOnInit(): void {
    this.currentUser = this.authService.getCurrentUser();
    this.isAdmin = this.authService.isAdmin();
    this.pushSupported = this.pushService.isSupported();

    this.accountForm = this.fb.group({
      fullName: [this.currentUser?.fullName || '', Validators.required],
      telegramUsername: [this.currentUser?.telegramUsername || '']
    });

    this.notifyForm = this.fb.group({
      notifyDigest: [this.currentUser?.notifyDigest ?? true],
      notifyFlash: [this.currentUser?.notifyFlash ?? true],
      notifyPush: [this.currentUser?.notifyPush ?? false]
    });

    this.passwordForm = this.fb.group({
      currentPassword: ['', Validators.required],
      newPassword: ['', [Validators.required, Validators.minLength(8)]],
      confirmNewPassword: ['', Validators.required]
    });

    this.pushService.isSubscribed().then(subscribed => {
      this.pushSubscribed = subscribed;
    });

    if (this.isAdmin) {
      this.loadAdminUsers();
    }
  }

  saveAccount(): void {
    if (this.accountForm.invalid) return;
    this.loading = true;
    this.clearMessages();

    this.authService.updateProfile(this.accountForm.value).subscribe({
      next: () => {
        this.loading = false;
        this.successMsg = 'Profile updated successfully.';
      },
      error: (err) => {
        this.loading = false;
        this.errorMsg = err?.error?.message || 'Failed to update profile.';
      }
    });
  }

  saveNotifications(): void {
    this.loading = true;
    this.clearMessages();

    this.authService.updateProfile(this.notifyForm.value).subscribe({
      next: () => {
        this.loading = false;
        this.successMsg = 'Notification preferences saved.';
      },
      error: (err) => {
        this.loading = false;
        this.errorMsg = err?.error?.message || 'Failed to save notification preferences.';
      }
    });
  }

  async enableBrowserPush(): Promise<void> {
    this.pushLoading = true;
    this.clearMessages();

    const result = await this.pushService.subscribe();
    this.pushLoading = false;

    if (result.success) {
      this.pushSubscribed = true;
      this.notifyForm.patchValue({ notifyPush: true });
      this.successMsg = result.message;
      this.authService.updateProfile({ notifyPush: true }).subscribe();
    } else {
      this.errorMsg = result.message;
    }
  }

  async disableBrowserPush(): Promise<void> {
    this.pushLoading = true;
    this.clearMessages();

    await this.pushService.unsubscribe();
    this.pushLoading = false;
    this.pushSubscribed = false;
    this.notifyForm.patchValue({ notifyPush: false });
    this.successMsg = 'Browser push notifications disabled.';
    this.authService.updateProfile({ notifyPush: false }).subscribe();
  }

  changePassword(): void {
    if (this.passwordForm.invalid) {
      this.passwordForm.markAllAsTouched();
      return;
    }
    const { currentPassword, newPassword, confirmNewPassword } = this.passwordForm.value;
    if (newPassword !== confirmNewPassword) {
      this.errorMsg = 'New passwords do not match.';
      return;
    }
    this.loading = true;
    this.clearMessages();

    this.authService.changePassword(currentPassword, newPassword).subscribe({
      next: () => {
        this.loading = false;
        this.successMsg = 'Password changed successfully.';
        this.passwordForm.reset();
      },
      error: (err) => {
        this.loading = false;
        this.errorMsg = err?.error?.message || 'Failed to change password.';
      }
    });
  }

  loadAdminUsers(): void {
    this.adminLoading = true;
    this.http.get<User[]>('/api/admin/users').subscribe({
      next: (users) => {
        this.adminLoading = false;
        this.adminUsers = users;
      },
      error: (err) => {
        this.adminLoading = false;
        this.adminError = err?.error?.message || 'Failed to load users.';
      }
    });
  }

  updateUserRole(userId: string, role: string): void {
    this.http.put(`/api/admin/users/${userId}`, { role }).subscribe({
      next: () => this.loadAdminUsers(),
      error: (err) => { this.adminError = err?.error?.message || 'Failed to update user.'; }
    });
  }

  updateUserStatus(userId: string, status: string): void {
    this.http.put(`/api/admin/users/${userId}`, { status }).subscribe({
      next: () => this.loadAdminUsers(),
      error: (err) => { this.adminError = err?.error?.message || 'Failed to update user.'; }
    });
  }

  deleteUser(userId: string): void {
    if (!confirm('Are you sure you want to delete this user?')) return;
    this.http.delete(`/api/admin/users/${userId}`).subscribe({
      next: () => this.loadAdminUsers(),
      error: (err) => { this.adminError = err?.error?.message || 'Failed to delete user.'; }
    });
  }

  private clearMessages(): void {
    this.errorMsg = '';
    this.successMsg = '';
  }
}
