'use client';

import { useActionState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { addUserSource, removeUserSource } from '@/lib/actions';
import type { SourceMutationState } from '@/lib/source-vault';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

const INITIAL: SourceMutationState = { status: 'idle', message: '' };
const State = ({ value }: { value: SourceMutationState }) => value.status === 'idle' ? null : (
  <p role="status" className={value.status === 'error' ? 'text-sm text-destructive' : 'text-sm text-muted-foreground'}>{value.message}</p>
);

export function AddSourceForm() {
  const [state, action, pending] = useActionState(addUserSource, INITIAL);
  return <form action={action} className="space-y-3">
    <div className="grid gap-3 lg:grid-cols-[1fr_1.5fr_9rem_auto] lg:items-center">
      <Input name="name" placeholder="Source name" required />
      <Input name="url" type="url" placeholder="https://example.com/feed" required />
      <select name="site_type" defaultValue="rss" className="h-10 rounded-md border border-input bg-background px-3 text-sm">
        <option value="rss">RSS feed</option><option value="website">Website</option>
      </select>
      <Button type="submit" className="gap-2" disabled={pending}><Plus className="h-4 w-4" />{pending ? 'Adding…' : 'Add source'}</Button>
    </div>
    <Input name="notes" placeholder="Notes (optional)" />
    <State value={state} />
  </form>;
}

export function RemoveSourceForm({ siteId, name }: { siteId: string; name: string }) {
  const [state, action, pending] = useActionState(removeUserSource, INITIAL);
  return <form action={action}><input type="hidden" name="site_id" value={siteId} />
    <button type="submit" disabled={pending} className="text-muted-foreground hover:text-destructive" aria-label={`Remove ${name}`}>
      <Trash2 className="h-4 w-4" />
    </button><State value={state} /></form>;
}
