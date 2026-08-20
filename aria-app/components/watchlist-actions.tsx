'use client';

import { useActionState } from 'react';
import { Plus, RefreshCw, Trash2 } from 'lucide-react';
import { addWatchlist, removeWatchlist, rescreenWatchlist } from '@/lib/actions';
import type { WatchlistMutationState } from '@/lib/watchlist-mutation';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

const INITIAL: WatchlistMutationState = { status: 'idle', message: '' };
const State = ({ value }: { value: WatchlistMutationState }) => value.status === 'idle' ? null : (
  <p role="status" className={value.status === 'error' ? 'text-sm text-destructive' : 'text-sm text-muted-foreground'}>{value.message}</p>
);

export function AddWatchlistForm() {
  const [state, action, pending] = useActionState(addWatchlist, INITIAL);
  return <form action={action} className="space-y-3"><div className="flex flex-col gap-3 sm:flex-row sm:items-center">
    <Input name="name" placeholder="Entity or company name…" className="sm:max-w-xs" required />
    <Input name="entity_type" placeholder="Type (optional)" className="sm:max-w-[10rem]" />
    <Input name="jurisdiction" placeholder="Jurisdiction (optional)" className="sm:max-w-[12rem]" />
    <Button type="submit" className="gap-2" disabled={pending}><Plus className="h-4 w-4" />{pending ? 'Adding…' : 'Add to watchlist'}</Button>
  </div><State value={state} /></form>;
}

export function RescreenWatchlistForm() {
  const [state, action, pending] = useActionState(rescreenWatchlist, INITIAL);
  return <form action={action} className="space-y-2">
    <Button type="submit" variant="outline" className="gap-2" disabled={pending}><RefreshCw className="h-4 w-4" />{pending ? 'Starting…' : 'Re-screen all'}</Button>
    <State value={state} />
  </form>;
}

export function RemoveWatchlistForm({ name }: { name: string }) {
  const [state, action, pending] = useActionState(removeWatchlist, INITIAL);
  return <form action={action}><input type="hidden" name="name" value={name} />
    <button type="submit" disabled={pending} className="text-muted-foreground hover:text-destructive" aria-label={`Remove ${name}`}>
      <Trash2 className="h-4 w-4" />
    </button><State value={state} /></form>;
}
