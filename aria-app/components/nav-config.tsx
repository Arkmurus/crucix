import {
  LayoutDashboard,
  FileSearch,
  TrendingUp,
  Eye,
  FolderLock,
  MessageSquare,
  CreditCard,
  LifeBuoy,
  Users,
  Inbox,
  Gauge,
  BrainCircuit,
  Wrench,
  Palette,
  Flag,
  Activity,
  type LucideIcon,
} from 'lucide-react';
import type { Role } from '@/lib/auth';

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

// Per-panel navigation, shared by the shell. Icons are lucide-react (the app's
// single icon set) so every panel reads as one design language.
export const NAV: Record<Role, NavItem[]> = {
  customer: [
    { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { href: '/reports', label: 'DD Reports', icon: FileSearch },
    { href: '/opportunities', label: 'Opportunities', icon: TrendingUp },
    { href: '/watchlist', label: 'Watchlist', icon: Eye },
    { href: '/vault', label: 'Vault', icon: FolderLock },
    { href: '/chat', label: 'Ask ARIA', icon: MessageSquare },
    { href: '/account', label: 'Account & Billing', icon: CreditCard },
  ],
  support: [
    { href: '/support', label: 'Overview', icon: LifeBuoy },
    { href: '/accounts', label: 'Customer Accounts', icon: Users },
    { href: '/tickets', label: 'Tickets', icon: Inbox },
  ],
  admin: [
    { href: '/admin', label: 'Overview', icon: Gauge },
    { href: '/brain', label: 'Brain', icon: BrainCircuit },
    { href: '/gaps', label: 'Gaps / Self-coding', icon: Wrench },
    { href: '/design', label: 'Design', icon: Palette },
    { href: '/flags', label: 'Feature Flags', icon: Flag },
    { href: '/users', label: 'Users', icon: Users },
    { href: '/status', label: 'System Status', icon: Activity },
  ],
};

export const PANEL_LABEL: Record<Role, string> = {
  customer: 'ARIA',
  support: 'ARIA · Support',
  admin: 'ARIA · Admin',
};
