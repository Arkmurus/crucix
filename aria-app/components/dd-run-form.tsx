'use client';

import { useActionState } from 'react';
import { useFormStatus } from 'react-dom';
import { Play } from 'lucide-react';
import { runDD } from '@/lib/actions';
import type { DDSubmissionState } from '@/lib/dd-submission';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

const INITIAL_STATE: DDSubmissionState = { status: 'idle', message: '' };

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" className="gap-2" disabled={pending}>
      <Play className="h-4 w-4" /> {pending ? 'Starting…' : 'Run due diligence'}
    </Button>
  );
}

/** Customer DD form with explicit, verified submission outcomes. */
export function DDRunForm() {
  const [state, action] = useActionState(runDD, INITIAL_STATE);
  return (
    <form action={action} className="space-y-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <Input name="name" placeholder="Entity or company name…" className="sm:max-w-xs" required />
        <Input name="jurisdiction" placeholder="Jurisdiction (optional)" className="sm:max-w-[12rem]" />
        <select name="mode" className="h-10 rounded-md border border-input bg-background px-3 text-sm">
          <option value="standard">Standard</option>
          <option value="deep">Deep</option>
        </select>
        <SubmitButton />
      </div>
      {state.status !== 'idle' ? (
        <p role="status" className={state.status === 'error' ? 'text-sm text-destructive' : 'text-sm text-muted-foreground'}>
          {state.message}{state.status === 'started' ? ' It will appear below while ARIA works.' : ''}
        </p>
      ) : null}
    </form>
  );
}
