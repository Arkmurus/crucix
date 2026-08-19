import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { AppShell } from '@/components/app-shell';
import { decodeToken, TOKEN_COOKIE } from '@/lib/auth';

export default async function CustomerLayout({ children }: { children: React.ReactNode }) {
  const user = decodeToken((await cookies()).get(TOKEN_COOKIE)?.value);
  if (!user) redirect('/signin');
  return (
    <AppShell role="customer" email={user.email}>
      {children}
    </AppShell>
  );
}
