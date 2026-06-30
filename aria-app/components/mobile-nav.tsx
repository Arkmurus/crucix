'use client';

import { useState, useEffect } from 'react';
import { usePathname } from 'next/navigation';
import { Menu, X, LogOut } from 'lucide-react';
import { SidebarNav } from '@/components/sidebar-nav';
import { PANEL_LABEL } from '@/components/nav-config';
import type { Role } from '@/lib/auth';

// Mobile navigation: a hamburger in the header (visible < md) that opens a slide-in
// drawer reusing SidebarNav, so phones get the same navigation as the desktop sidebar.
export function MobileNav({ role }: { role: Role }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  // Close the drawer on route change.
  useEffect(() => setOpen(false), [pathname]);

  return (
    <div className="md:hidden">
      <button
        onClick={() => setOpen(true)}
        aria-label="Open menu"
        className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground"
      >
        <Menu className="h-5 w-5" />
      </button>

      {open ? (
        <div className="fixed inset-0 z-50">
          <div className="absolute inset-0 bg-foreground/20" onClick={() => setOpen(false)} aria-hidden />
          <div className="absolute left-0 top-0 flex h-full w-64 flex-col border-r bg-card shadow-xl">
            <div className="flex h-16 items-center justify-between px-4">
              <span className="flex items-center gap-2 text-lg font-semibold tracking-tight">
                <span className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-sm font-bold text-primary-foreground">A</span>
                {PANEL_LABEL[role]}
              </span>
              <button onClick={() => setOpen(false)} aria-label="Close menu" className="text-muted-foreground hover:text-foreground">
                <X className="h-5 w-5" />
              </button>
            </div>
            <SidebarNav role={role} />
            <form action="/api/session" method="post" className="border-t p-3">
              <input type="hidden" name="action" value="logout" />
              <button className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground">
                <LogOut className="h-4 w-4 shrink-0" /> Sign out
              </button>
            </form>
          </div>
        </div>
      ) : null}
    </div>
  );
}
