'use client';

import { useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { decodeToken, homeForRole } from '@/lib/auth';

const clientBackendBase = process.env.NEXT_PUBLIC_BACKEND_URL || '';

interface AuthUser { mustChangePassword?: boolean }
interface AuthResponse {
  token?: string;
  user?: AuthUser;
  requires2FA?: boolean;
  preToken?: string;
  error?: string;
}

async function responseBody(res: Response): Promise<AuthResponse> {
  try { return (await res.json()) as AuthResponse; } catch { return {}; }
}

export function SignInForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [preToken, setPreToken] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function completeSignIn(data: AuthResponse) {
    if (!data.token) throw new Error('missing_session_token');
    const sessionRes = await fetch('/api/session', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ token: data.token }),
    });
    if (!sessionRes.ok) throw new Error('session_not_persisted');

    try {
      localStorage.setItem('crucix_token', data.token);
      if (data.user) localStorage.setItem('crucix_user', JSON.stringify(data.user));
    } catch {
      // The httpOnly cookie remains authoritative for native pages.
    }

    const user = decodeToken(data.token);
    const rawNext = params.get('next');
    const safeNext = rawNext && /^\/(?!\/)/.test(rawNext) ? rawNext : null;
    const destination = data.user?.mustChangePassword
      ? '/set-password.html'
      : safeNext || (user ? homeForRole(user.role) : '/dashboard');
    router.push(destination);
    router.refresh();
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (preToken) {
        const res = await fetch(clientBackendBase + '/api/auth/2fa/authenticate', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ preToken, code }),
        });
        const data = await responseBody(res);
        if (!res.ok) {
          setError(data.error || 'Invalid or expired authentication code.');
          return;
        }
        await completeSignIn(data);
        return;
      }

      const res = await fetch(clientBackendBase + '/api/auth/login', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      const data = await responseBody(res);
      if (!res.ok) {
        setError(data.error || 'Sign in failed. Check your credentials.');
        return;
      }
      if (data.requires2FA) {
        if (!data.preToken) throw new Error('missing_pre_auth_token');
        setPreToken(data.preToken);
        setPassword('');
        return;
      }
      await completeSignIn(data);
    } catch {
      setError('Sign in could not be completed. Try again.');
    } finally {
      setBusy(false);
    }
  }

  function backToCredentials() {
    setPreToken(null);
    setCode('');
    setError(null);
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle className="text-2xl">{preToken ? 'Two-factor authentication' : 'Welcome to ARIA'}</CardTitle>
        <CardDescription>
          {preToken ? 'Enter the six-digit code from your authenticator app.' : 'Sign in to your account.'}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="space-y-4">
          {preToken ? (
            <div className="space-y-2">
              <Label htmlFor="two-factor-code">Authentication code</Label>
              <Input
                id="two-factor-code"
                autoComplete="one-time-code"
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                required
                autoFocus
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              />
            </div>
          ) : (
            <>
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
              </div>
            </>
          )}
          {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? (preToken ? 'Verifying…' : 'Signing in…') : (preToken ? 'Verify code' : 'Sign in')}
          </Button>
          {preToken ? (
            <Button type="button" variant="ghost" className="w-full" disabled={busy} onClick={backToCredentials}>
              Back to sign in
            </Button>
          ) : null}
        </form>
      </CardContent>
    </Card>
  );
}
