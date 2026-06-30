import { LogOut } from 'lucide-react';
import { SidebarNav } from '@/components/sidebar-nav';
import { PANEL_LABEL } from '@/components/nav-config';
import type { Role } from '@/lib/auth';

export function AppShell({
  role,
  email,
  children,
}: {
  role: Role;
  email?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-64 shrink-0 flex-col border-r bg-card md:flex">
        <div className="flex h-16 items-center gap-2 px-6 text-lg font-semibold tracking-tight">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-sm font-bold text-primary-foreground">
            A
          </span>
          {PANEL_LABEL[role]}
        </div>
        <SidebarNav role={role} />
        <form action="/api/session" method="post" className="border-t p-3">
          <input type="hidden" name="action" value="logout" />
          <button className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground">
            <LogOut className="h-4 w-4 shrink-0" />
            Sign out
          </button>
        </form>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 items-center justify-between border-b bg-background/80 px-6 backdrop-blur">
          <span className="text-sm capitalize text-muted-foreground">{role} panel</span>
          {email ? (
            <span className="flex items-center gap-2 text-sm text-muted-foreground">
              <span className="flex h-7 w-7 items-center justify-center rounded-full bg-muted text-xs font-medium uppercase text-foreground">
                {email.slice(0, 1)}
              </span>
              {email}
            </span>
          ) : null}
        </header>
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
