import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { AppShell } from '@/components/app-shell';
import { decodeToken, roleAllows, TOKEN_COOKIE } from '@/lib/auth';

export default async function SupportLayout({ children }: { children: React.ReactNode }) {
  const user = decodeToken((await cookies()).get(TOKEN_COOKIE)?.value);
  if (!user) redirect('/signin');
  if (!roleAllows(user.role, ['support'])) redirect('/dashboard');
  return (
    <AppShell role="support" email={user.email}>
      {children}
    </AppShell>
  );
}
