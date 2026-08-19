import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { AppShell } from '@/components/app-shell';
import { decodeToken, roleAllows, TOKEN_COOKIE } from '@/lib/auth';

export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  const user = decodeToken((await cookies()).get(TOKEN_COOKIE)?.value);
  if (!user) redirect('/signin');
  if (!roleAllows(user.role, ['admin'])) redirect('/dashboard');
  return (
    <AppShell role="admin" email={user.email}>
      {children}
    </AppShell>
  );
}
