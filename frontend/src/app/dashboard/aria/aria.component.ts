import { Component, OnInit, ViewChild, ElementRef, AfterViewChecked } from '@angular/core';
import { CrucixApiService } from '../../services/crucix-api.service';

interface ChatMessage {
  role: 'user' | 'aria';
  content: string;
  timestamp: string;
  confidence?: number;
  epistemic?: string;
  selfGrade?: string;
  isThinking?: boolean;
}

@Component({
  selector: 'app-aria',
  templateUrl: './aria.component.html',
  styleUrls: ['./aria.component.scss']
})
export class AriaComponent implements OnInit, AfterViewChecked {
  @ViewChild('chatScroll') chatScroll!: ElementRef;
  @ViewChild('msgInput')   msgInput!: ElementRef;

  identity: any = null;
  thoughts: any[] = [];
  curiosity: any[] = [];

  messages: ChatMessage[] = [];
  inputText = '';
  sending = false;
  thinkMode = false;
  sessionId = `aria_${Date.now()}`;
  sidePanel: 'identity' | 'thoughts' | 'curiosity' | null = null;

  private shouldScroll = false;

  constructor(private api: CrucixApiService) {}

  ngOnInit(): void {
    this.api.getAriaIdentity().subscribe(id  => { this.identity  = id; });
    this.api.getAriaThoughts().subscribe(t   => { this.thoughts  = t || []; });
    this.api.getAriaCuriosity().subscribe(c  => { this.curiosity = c?.open_threads || []; });

    this.messages = [{
      role: 'aria',
      content: 'I am ARIA — Arkmurus Research Intelligence Agent. I reason about defence procurement intelligence, Lusophone Africa markets, and export control compliance.\n\nAsk me anything, or switch to Think Mode for deep 6-step analysis with self-critique.',
      timestamp: new Date().toISOString(),
    }];
  }

  ngAfterViewChecked(): void {
    if (this.shouldScroll && this.chatScroll) {
      const el = this.chatScroll.nativeElement;
      el.scrollTop = el.scrollHeight;
      this.shouldScroll = false;
    }
  }

  togglePanel(panel: 'identity' | 'thoughts' | 'curiosity'): void {
    this.sidePanel = this.sidePanel === panel ? null : panel;
  }

  send(): void {
    const msg = this.inputText.trim();
    if (!msg || this.sending) return;

    this.messages.push({ role: 'user', content: msg, timestamp: new Date().toISOString() });
    this.messages.push({ role: 'aria', content: '', timestamp: new Date().toISOString(), isThinking: true });
    this.inputText = '';
    this.sending = true;
    this.shouldScroll = true;

    // Reset textarea height
    if (this.msgInput) {
      this.msgInput.nativeElement.style.height = 'auto';
    }

    if (this.thinkMode) {
      this.api.ariaThink(msg, {}, false).subscribe({
        next: res  => { this.replaceThinking(res); this.sending = false; this.shouldScroll = true; },
        error: err => { this.replaceThinking({ error: err.message || 'Request failed' }); this.sending = false; },
      });
    } else {
      this.api.ariaChat(msg, this.sessionId).subscribe({
        next: res  => { this.replaceThinking({ response: res.response, fallback: res.fallback }); this.sending = false; this.shouldScroll = true; },
        error: err => { this.replaceThinking({ error: err.message || 'Request failed' }); this.sending = false; },
      });
    }
  }

  private replaceThinking(res: any): void {
    let idx = -1;
    for (let i = this.messages.length - 1; i >= 0; i--) {
      if (this.messages[i].isThinking) { idx = i; break; }
    }
    if (idx === -1) return;

    if (res.error) {
      this.messages[idx] = { role: 'aria', content: `⚠ ${res.error}`, timestamp: new Date().toISOString() };
      return;
    }

    if (res.conclusion) {
      const c = res.conclusion;
      const m = res.metacognition || {};
      const parts: string[] = [c.statement || JSON.stringify(c)];
      if (c.key_assumption)   parts.push(`\n\n**Key assumption:** ${c.key_assumption}`);
      if (c.action?.what)     parts.push(`\n\n**Action:** ${c.action.what}`);
      if (m.biggest_gap)      parts.push(`\n\n**Biggest gap:** ${m.biggest_gap}`);
      this.messages[idx] = {
        role: 'aria',
        content:    parts.join(''),
        timestamp:  new Date().toISOString(),
        confidence: c.confidence,
        epistemic:  c.epistemic_status,
        selfGrade:  m.self_grade,
      };
    } else {
      this.messages[idx] = {
        role: 'aria',
        content:   res.response || 'No response received.',
        timestamp: new Date().toISOString(),
      };
    }
  }

  autoGrow(el: HTMLTextAreaElement): void {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }

  clearChat(): void {
    this.sessionId = `aria_${Date.now()}`;
    this.messages = [{
      role: 'aria',
      content: 'New session started. What would you like to explore?',
      timestamp: new Date().toISOString(),
    }];
  }

  formatTime(iso: string): string {
    try {
      return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    } catch { return ''; }
  }

  epistemicColor(e: string): string {
    if (e === 'CONFIRMED') return '#4caf50';
    if (e === 'PROBABLE')  return '#8bc34a';
    if (e === 'ASSESSED')  return '#ff9800';
    if (e === 'UNCERTAIN') return '#ef5350';
    return '#78909c';
  }

  gradeColor(g: string): string {
    if (g === 'A') return '#4caf50';
    if (g === 'B') return '#8bc34a';
    if (g === 'C') return '#ff9800';
    return '#ef5350';
  }

  confidenceColor(c: number): string {
    if (c >= 70) return '#4caf50';
    if (c >= 45) return '#ff9800';
    return '#ef5350';
  }
}
