'use client';

import { useRef, useState } from 'react';
import { Send, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

interface Msg { role: 'user' | 'assistant'; text: string }

export default function ChatPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const sessionId = useRef<string>(`aria-app-${Math.random().toString(36).slice(2)}`);
  const scrollRef = useRef<HTMLDivElement>(null);

  function scrollToEnd() {
    requestAnimationFrame(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }));
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const message = input.trim();
    if (!message || busy) return;
    setInput('');
    setBusy(true);
    setStatus(null);
    setMessages((m) => [...m, { role: 'user', text: message }, { role: 'assistant', text: '' }]);
    scrollToEnd();

    try {
      const res = await fetch('/api/aria/chat/stream', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ message, session_id: sessionId.current }),
      });
      if (!res.ok || !res.body) {
        appendToLast(res.status === 401 ? '⚠️ Your session expired — please sign in again.' : '⚠️ ARIA is unavailable right now.');
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith('data:')) continue;
          const json = line.slice(5).trim();
          if (!json) continue;
          try {
            const evt = JSON.parse(json) as { type?: string; text?: string; message?: string; reason?: string };
            if (evt.type === 'chunk' && evt.text) {
              appendToLast(evt.text);
              setStatus(null);
            } else if (evt.type === 'status' || evt.type === 'progress') {
              setStatus(evt.message || null);
            } else if (evt.type === 'error') {
              appendToLast(`\n\n⚠️ ${evt.message || evt.reason || 'Something went wrong.'}`);
            } else if (evt.type === 'done') {
              setStatus(null);
            }
          } catch {
            /* ignore keepalive / non-JSON lines */
          }
          scrollToEnd();
        }
      }
    } catch {
      appendToLast('⚠️ Connection lost. Please try again.');
    } finally {
      setBusy(false);
      setStatus(null);
      scrollToEnd();
    }
  }

  function appendToLast(text: string) {
    setMessages((m) => {
      const copy = [...m];
      const last = copy[copy.length - 1];
      if (last && last.role === 'assistant') copy[copy.length - 1] = { ...last, text: last.text + text };
      return copy;
    });
  }

  return (
    <div className="mx-auto flex h-[calc(100vh-7rem)] max-w-3xl flex-col">
      <div className="mb-4">
        <h1 className="text-2xl font-semibold tracking-tight">Ask ARIA</h1>
        <p className="text-sm text-muted-foreground">Intelligence, due diligence and compliance — grounded, no fabrication.</p>
      </div>

      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto rounded-xl border bg-card p-4">
        {messages.length === 0 ? (
          <p className="mt-10 text-center text-sm text-muted-foreground">
            Ask about a company, jurisdiction, sanction, or market signal to get started.
          </p>
        ) : (
          messages.map((m, i) => (
            <div key={i} className={cn('flex', m.role === 'user' ? 'justify-end' : 'justify-start')}>
              <div
                className={cn(
                  'max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm',
                  m.role === 'user' ? 'bg-primary text-primary-foreground' : 'bg-muted text-foreground',
                )}
              >
                {m.text || (busy && i === messages.length - 1 ? <Loader2 className="h-4 w-4 animate-spin" /> : null)}
              </div>
            </div>
          ))
        )}
        {status ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> {status}
          </div>
        ) : null}
      </div>

      <form onSubmit={send} className="mt-4 flex gap-2">
        <Input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Message ARIA…" disabled={busy} autoFocus />
        <Button type="submit" disabled={busy || !input.trim()} className="gap-2">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          Send
        </Button>
      </form>
    </div>
  );
}
