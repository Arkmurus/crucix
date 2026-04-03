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

  identity: any = null;
  thoughts: any[] = [];
  curiosity: any[] = [];

  messages: ChatMessage[] = [];
  inputText = '';
  sending = false;
  activeTab: 'chat' | 'identity' | 'thoughts' | 'curiosity' = 'chat';
  thinkMode = false; // deep reasoning vs fast chat
  sessionId = `aria_${Date.now()}`;
  private shouldScroll = false;

  constructor(private api: CrucixApiService) {}

  ngOnInit(): void {
    this.api.getAriaIdentity().subscribe(id => { this.identity = id; });
    this.api.getAriaThoughts().subscribe(t => { this.thoughts = t || []; });
    this.api.getAriaCuriosity().subscribe(c => { this.curiosity = c?.open_threads || []; });

    // Welcome message
    this.messages = [{
      role: 'aria',
      content: 'I am ARIA — Arkmurus Research Intelligence Agent. I reason about defence procurement intelligence, Lusophone Africa markets, and export control compliance. Ask me anything, or use Think Mode for deep multi-step analysis.',
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

  send(): void {
    const msg = this.inputText.trim();
    if (!msg || this.sending) return;

    this.messages.push({ role: 'user', content: msg, timestamp: new Date().toISOString() });
    this.messages.push({ role: 'aria', content: '', timestamp: new Date().toISOString(), isThinking: true });
    this.inputText = '';
    this.sending = true;
    this.shouldScroll = true;

    if (this.thinkMode) {
      this.api.ariaThink(msg, {}, false).subscribe(res => {
        this.replaceThinking(res);
        this.sending = false;
        this.shouldScroll = true;
      });
    } else {
      this.api.ariaChat(msg, this.sessionId).subscribe(res => {
        this.replaceThinking({ response: res.response, fallback: res.fallback });
        this.sending = false;
        this.shouldScroll = true;
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
      this.messages[idx] = { role: 'aria', content: res.error, timestamp: new Date().toISOString() };
      return;
    }

    // Deep think response
    if (res.conclusion) {
      const c = res.conclusion;
      const m = res.metacognition || {};
      const content = [
        c.statement || JSON.stringify(c),
        c.key_assumption ? `\n\nKey assumption: ${c.key_assumption}` : '',
        c.action?.what ? `\n\nAction: ${c.action.what}` : '',
        m.biggest_gap ? `\n\nBiggest gap: ${m.biggest_gap}` : '',
      ].join('');
      this.messages[idx] = {
        role: 'aria', content,
        timestamp: new Date().toISOString(),
        confidence: c.confidence,
        epistemic: c.epistemic_status,
        selfGrade: m.self_grade,
      };
    } else {
      // Chat response
      this.messages[idx] = {
        role: 'aria',
        content: res.response || 'No response',
        timestamp: new Date().toISOString(),
      };
    }
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.send();
    }
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

  clearChat(): void {
    this.sessionId = `aria_${Date.now()}`;
    this.messages = [{
      role: 'aria',
      content: 'New session started. What would you like to explore?',
      timestamp: new Date().toISOString(),
    }];
  }
}
