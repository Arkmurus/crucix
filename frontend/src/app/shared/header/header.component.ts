import { Component, OnInit, OnDestroy } from '@angular/core';
import { Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { SidebarService } from '../sidebar/sidebar.service';
import { AuthService, User } from '../../services/auth.service';

@Component({
  selector: 'app-header',
  templateUrl: './header.component.html',
  styleUrls: ['./header.component.scss']
})
export class HeaderComponent implements OnInit, OnDestroy {
  theme_name = 'dark_mode';
  toggleSearch = false;
  currentUser: User | null = null;

  private sub: Subscription | null = null;

  constructor(
    public sidebarservice: SidebarService,
    public auth: AuthService,
    private router: Router
  ) {}

  ngOnInit(): void {
    this.sub = this.auth.currentUser$.subscribe(u => this.currentUser = u);
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
  }

  get userInitials(): string {
    if (!this.currentUser) return '?';
    const parts = (this.currentUser.fullName || this.currentUser.username || '?').split(' ');
    return parts.length >= 2
      ? (parts[0][0] + parts[1][0]).toUpperCase()
      : parts[0].substring(0, 2).toUpperCase();
  }

  get displayName(): string {
    return this.currentUser?.fullName || this.currentUser?.username || 'User';
  }

  get displayEmail(): string {
    return this.currentUser?.email || '';
  }

  get roleBadge(): string {
    const r = this.currentUser?.role;
    if (r === 'admin') return 'Admin';
    if (r === 'analyst') return 'Analyst';
    return 'Viewer';
  }

  logout(): void {
    this.auth.logout();
    this.router.navigate(['/auth/sign-in']);
  }

  goProfile(): void {
    this.router.navigate(['/profile/user-profile']);
  }

  goDashboard(): void {
    this.router.navigate(['/dashboard/brief']);
  }

  darkMode(): void {
    if (this.theme_name === 'dark_mode') {
      document.querySelector('html')!.classList.replace('light_mode', 'dark_mode');
      this.theme_name = 'light_mode';
    } else {
      document.querySelector('html')!.classList.replace('dark_mode', 'light_mode');
      this.theme_name = 'dark_mode';
    }
  }

  getSideBarSate() {
    return this.sidebarservice.getSidebarState();
  }

  toggleSidebar() {
    this.sidebarservice.setSidebarState(!this.sidebarservice.getSidebarState());
  }

  openSearch()  { this.toggleSearch = true; }
  searchClose() { this.toggleSearch = false; }
}
