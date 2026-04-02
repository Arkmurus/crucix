import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { BehaviorSubject, Observable, tap } from 'rxjs';

export interface User {
  id: string;
  username: string;
  email: string;
  fullName: string;
  role: 'admin' | 'analyst' | 'viewer';
  status: string;
  telegramUsername?: string;
  notifyDigest: boolean;
  notifyFlash: boolean;
  notifyPush: boolean;
  twoFactorEnabled?: boolean;
  createdAt: string;
  lastLogin?: string;
}

@Injectable({
  providedIn: 'root'
})
export class AuthService {
  private readonly TOKEN_KEY = 'ark_token';
  private readonly USER_KEY = 'ark_user';

  private currentUserSubject = new BehaviorSubject<User | null>(null);
  public currentUser$: Observable<User | null> = this.currentUserSubject.asObservable();

  constructor(private http: HttpClient) {
    this.loadFromStorage();
  }

  private loadFromStorage(): void {
    const token = localStorage.getItem(this.TOKEN_KEY);
    const userStr = localStorage.getItem(this.USER_KEY);
    if (token && userStr) {
      try {
        const user: User = JSON.parse(userStr);
        this.currentUserSubject.next(user);
      } catch {
        this.clearStorage();
      }
    }
  }

  private clearStorage(): void {
    localStorage.removeItem(this.TOKEN_KEY);
    localStorage.removeItem(this.USER_KEY);
    this.currentUserSubject.next(null);
  }

  getToken(): string | null {
    return localStorage.getItem(this.TOKEN_KEY);
  }

  getCurrentUser(): User | null {
    return this.currentUserSubject.value;
  }

  isLoggedIn(): boolean {
    return !!this.getToken() && !!this.getCurrentUser();
  }

  isAdmin(): boolean {
    const user = this.getCurrentUser();
    return user?.role === 'admin';
  }

  login(email: string, password: string): Observable<any> {
    return this.http.post<any>('/api/auth/login', { email, password }).pipe(
      tap(res => {
        if (!res.requires2FA) {
          localStorage.setItem(this.TOKEN_KEY, res.token);
          localStorage.setItem(this.USER_KEY, JSON.stringify(res.user));
          this.currentUserSubject.next(res.user);
        }
      })
    );
  }

  twoFaAuthenticate(preToken: string, code: string): Observable<any> {
    return this.http.post<any>('/api/auth/2fa/authenticate', { preToken, code }).pipe(
      tap(res => {
        localStorage.setItem(this.TOKEN_KEY, res.token);
        localStorage.setItem(this.USER_KEY, JSON.stringify(res.user));
        this.currentUserSubject.next(res.user);
      })
    );
  }

  twoFaSetup(): Observable<{ secret: string; qrDataUrl: string }> {
    return this.http.post<any>('/api/auth/2fa/setup', {});
  }

  twoFaEnable(code: string): Observable<any> {
    return this.http.post('/api/auth/2fa/enable', { code });
  }

  twoFaDisable(code: string): Observable<any> {
    return this.http.post('/api/auth/2fa/disable', { code });
  }

  register(data: { fullName: string; username: string; email: string; password: string }): Observable<any> {
    return this.http.post('/api/auth/register', data);
  }

  verifyEmail(email: string, code: string): Observable<any> {
    return this.http.post('/api/auth/verify-email', { email, code });
  }

  resendVerification(email: string): Observable<any> {
    return this.http.post('/api/auth/resend-verification', { email });
  }

  logout(): void {
    this.clearStorage();
  }

  getMe(): Observable<User> {
    return this.http.get<User>('/api/auth/me').pipe(
      tap(user => {
        localStorage.setItem(this.USER_KEY, JSON.stringify(user));
        this.currentUserSubject.next(user);
      })
    );
  }

  updateProfile(data: {
    fullName?: string;
    telegramUsername?: string;
    notifyDigest?: boolean;
    notifyFlash?: boolean;
    notifyPush?: boolean;
  }): Observable<User> {
    return this.http.put<User>('/api/auth/profile', data).pipe(
      tap(user => {
        localStorage.setItem(this.USER_KEY, JSON.stringify(user));
        this.currentUserSubject.next(user);
      })
    );
  }

  changePassword(currentPassword: string, newPassword: string): Observable<any> {
    return this.http.put('/api/auth/password', { currentPassword, newPassword });
  }

  forgotPassword(email: string): Observable<any> {
    return this.http.post('/api/auth/forgot-password', { email });
  }

  resetPassword(email: string, code: string, newPassword: string): Observable<any> {
    return this.http.post('/api/auth/reset-password', { email, code, newPassword });
  }
}
