import { redirect } from 'next/navigation';
import { cookies } from 'next/headers';
import { decodeToken, homeForRole, TOKEN_COOKIE } from '@/lib/auth';

// Root: route the visitor to their panel, or to sign-in.
export default async function RootPage() {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  const user = decodeToken(token);
  if (!user) redirect('/signin');
  redirect(homeForRole(user.role));
}
