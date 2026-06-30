import Link from 'next/link';
import { cn } from '@/lib/utils';
import type { Role } from '@/lib/auth';

export interface NavItem {
  href: string;
  label: string;
}

// Per-panel navigation. Kept here so all three layouts share one shell (DRY).
export const NAV: Record<Role, NavItem[]> = {
  customer: [
    { href: '/dashboard', label: 'Dashboard' },
    { href: '/reports', label: 'DD Reports' },
    { href: '/vault', label: 'Vault' },
    { href: '/watchlist', label: 'Watchlist' },
    { href: '/chat', label: 'Ask ARIA' },
    { href: '/account', label: 'Account & Billing' },
  ],
  support: [
    { href: '/support', label: 'Overview' },
    { href: '/accounts', label: 'Customer Accounts' },
    { href: '/tickets', label: 'Tickets' },
  ],
  admin: [
    { href: '/admin', label: 'Overview' },
    { href: '/brain', label: 'Brain' },
    { href: '/gaps', label: 'Gaps / Self-coding' },
    { href: '/design', label: 'Design' },
    { href: '/flags', label: 'Feature Flags' },
    { href: '/users', label: 'Users' },
    { href: '/status', label: 'System Status' },
  ],
};

const PANEL_LABEL: Record<Role, string> = {
  customer: 'ARIA',
  support: 'ARIA · Support',
  admin: 'ARIA · Admin',
};

export function AppShell({
  role,
  email,
  children,
}: {
  role: Role;
  email?: string;
  children: React.ReactNode;
}) {
  const nav = NAV[role];
  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-64 shrink-0 flex-col border-r bg-card md:flex">
        <div className="flex h-16 items-center px-6 text-lg font-semibold tracking-tight">
          {PANEL_LABEL[role]}
        </div>
        <nav className="flex-1 space-y-1 px-3 py-2">
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'block rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground',
              )}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <form action="/api/session" method="post" className="border-t p-3">
          <input type="hidden" name="action" value="logout" />
          <button className="w-full rounded-md px-3 py-2 text-left text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground">
            Sign out
          </button>
        </form>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 items-center justify-between border-b px-6">
          <span className="text-sm text-muted-foreground capitalize">{role} panel</span>
          {email ? <span className="text-sm text-muted-foreground">{email}</span> : null}
        </header>
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
