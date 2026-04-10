import { Component, OnInit, OnDestroy, ViewChild, ElementRef, AfterViewChecked, ChangeDetectorRef } from '@angular/core';
import { CrucixApiService, AriaChatEvent } from '../../services/crucix-api.service';
import { AuthService } from '../../services/auth.service';
import { Subscription } from 'rxjs';

interface ChatMessage {
  role: 'user' | 'aria';
  content: string;
  timestamp: string;
  confidence?: number;
  epistemic?: string;
  selfGrade?: string;
  isThinking?: boolean;
  streaming?: boolean;
  statusText?: string;
}

interface Conversation {
  session_id: string;
  title: string;
  updatedAt: number;
  messageCount: number;
}

@Component({
  selector: 'app-aria',
  templateUrl: './aria.component.html',
  styleUrls: ['./aria.component.scss']
})
export class AriaComponent implements OnInit, OnDestroy, AfterViewChecked {
  @ViewChild('chatScroll') chatScroll!: ElementRef;
  @ViewChild('msgInput')   msgInput!: ElementRef;

  identity: any = null;
  thoughts: any[] = [];
  curiosity: any[] = [];

  messages: ChatMessage[] = [];
  inputText = '';
  sending = false;
  streaming = false;
  thinkMode = false;
  sessionId = '';
  sidePanel: 'identity' | 'thoughts' | 'curiosity' | null = null;

  // Conversation sidebar
  conversations: Conversation[] = [];
  activeConversationId = '';
  showConversations = true;
  renamingId = '';
  renameText = '';

  private shouldScroll = false;
  private streamSub: Subscription | null = null;
  private userId = '';

  constructor(
    private api: CrucixApiService,
    private auth: AuthService,
    private cdr: ChangeDetectorRef,
  ) {}

  ngOnInit(): void {
    const user = this.auth.getCurrentUser();
    this.userId = user?.id || 'anon';
    this.newConversation();

    this.api.getAriaIdentity().subscribe(id  => { this.identity  = id; });
    this.api.getAriaThoughts().subscribe(t   => { this.thoughts  = t || []; });
    this.api.getAriaCuriosity().subscribe(c  => { this.curiosity = c?.open_threads || []; });

    this.loadConversations();
  }

  ngOnDestroy(): void {
    this.streamSub?.unsubscribe();
    this.api.stopStream();
  }

  ngAfterViewChecked(): void {
    if (this.shouldScroll && this.chatScroll) {
      const el = this.chatScroll.nativeElement;
      el.scrollTop = el.scrollHeight;
      this.shouldScroll = false;
    }
  }

  // ── Conversation Management ─────────────────────────────────────────────

  loadConversations(): void {
    this.api.getConversations().subscribe(res => {
      this.conversations = (res?.conversations || []).map((c: any) => ({
        session_id: c.session_id,
        title: c.title || 'Untitled',
        updatedAt: c.updatedAt || 0,
        messageCount: c.messageCount || 0,
      }));
    });
  }

  newConversation(): void {
    this.sessionId = `${this.userId}_${Date.now()}`;
    this.activeConversationId = this.sessionId;
    this.messages = [{
      role: 'aria',
      content: 'I am ARIA — the world\'s most capable specialised intelligence system for global defence BD and compliance. I operate globally with deep expertise in defence procurement, export controls, and geopolitical intelligence.\n\nAsk me anything.',
      timestamp: new Date().toISOString(),
    }];
  }

  selectConversation(convo: Conversation): void {
    if (convo.session_id === this.activeConversationId) return;
    this.activeConversationId = convo.session_id;
    this.sessionId = convo.session_id;
    this.messages = [{ role: 'aria', content: 'Loading conversation...', timestamp: new Date().toISOString(), isThinking: true }];

    this.api.loadConversation(convo.session_id).subscribe(res => {
      if (res?.messages?.length) {
        this.messages = res.messages.map((m: any) => ({
          role: m.role === 'user' ? 'user' : 'aria',
          content: m.content,
          timestamp: new Date().toISOString(),
        }));
      } else {
        this.messages = [{ role: 'aria', content: 'No messages found.', timestamp: new Date().toISOString() }];
      }
      this.shouldScroll = true;
    });
  }

  deleteConversation(convo: Conversation, event: Event): void {
    event.stopPropagation();
    this.api.deleteConversation(convo.session_id).subscribe(() => {
      this.conversations = this.conversations.filter(c => c.session_id !== convo.session_id);
      if (this.activeConversationId === convo.session_id) {
        this.newConversation();
      }
    });
  }

  startRename(convo: Conversation, event: Event): void {
    event.stopPropagation();
    this.renamingId = convo.session_id;
    this.renameText = convo.title;
  }

  confirmRename(convo: Conversation): void {
    if (this.renameText.trim()) {
      this.api.renameConversation(convo.session_id, this.renameText.trim()).subscribe(() => {
        convo.title = this.renameText.trim();
        this.renamingId = '';
      });
    }
  }

  cancelRename(): void {
    this.renamingId = '';
  }

  togglePanel(panel: 'identity' | 'thoughts' | 'curiosity'): void {
    this.sidePanel = this.sidePanel === panel ? null : panel;
  }

  // ── Chat ─────────────────────────────────────────────────────────────────

  send(): void {
    const msg = this.inputText.trim();
    if (!msg || this.sending) return;

    this.messages.push({ role: 'user', content: msg, timestamp: new Date().toISOString() });
    this.inputText = '';
    this.sending = true;
    this.shouldScroll = true;

    // Reset textarea height
    if (this.msgInput) {
      this.msgInput.nativeElement.style.height = 'auto';
    }

    if (this.thinkMode) {
      // Think mode stays non-streaming (deep 6-step reasoning)
      this.messages.push({ role: 'aria', content: '', timestamp: new Date().toISOString(), isThinking: true });
      this.api.ariaThink(msg, {}, false).subscribe({
        next: res  => { this.replaceThinking(res); this.sending = false; this.shouldScroll = true; },
        error: err => { this.replaceThinking({ error: err.message || 'Request failed' }); this.sending = false; },
      });
    } else {
      // Streaming mode
      this.sendStreaming(msg);
    }
  }

  private sendStreaming(msg: string): void {
    this.streaming = true;
    const ariaMsg: ChatMessage = {
      role: 'aria',
      content: '',
      timestamp: new Date().toISOString(),
      streaming: true,
      statusText: 'Connecting...',
    };
    this.messages.push(ariaMsg);

    this.streamSub = this.api.ariaChatStream(msg, this.sessionId).subscribe({
      next: (event: AriaChatEvent) => {
        switch (event.type) {
          case 'status':
            ariaMsg.statusText = event.message || '';
            ariaMsg.isThinking = true;
            break;
          case 'chunk':
            ariaMsg.isThinking = false;
            ariaMsg.statusText = '';
            ariaMsg.content += event.text || '';
            this.shouldScroll = true;
            break;
          case 'done':
            ariaMsg.streaming = false;
            this.streaming = false;
            this.sending = false;
            this.shouldScroll = true;
            // Refresh conversation list
            this.loadConversations();
            break;
          case 'error':
            ariaMsg.streaming = false;
            ariaMsg.isThinking = false;
            ariaMsg.content = ariaMsg.content || `Error: ${event.message || 'Stream failed'}`;
            this.streaming = false;
            this.sending = false;
            break;
          case 'meta':
            // Supplementary metadata (tool_used, etc.)
            break;
        }
        this.cdr.detectChanges();
      },
      error: () => {
        ariaMsg.streaming = false;
        ariaMsg.content = ariaMsg.content || 'Connection lost.';
        this.streaming = false;
        this.sending = false;
        this.cdr.detectChanges();
      },
      complete: () => {
        ariaMsg.streaming = false;
        this.streaming = false;
        this.sending = false;
        this.cdr.detectChanges();
      },
    });
  }

  stopGeneration(): void {
    this.api.stopStream();
    this.streaming = false;
    this.sending = false;
    // Mark the last message as not streaming
    const last = this.messages[this.messages.length - 1];
    if (last?.streaming) {
      last.streaming = false;
      last.content += '\n\n*[Generation stopped]*';
    }
  }

  private replaceThinking(res: any): void {
    let idx = -1;
    for (let i = this.messages.length - 1; i >= 0; i--) {
      if (this.messages[i].isThinking) { idx = i; break; }
    }
    if (idx === -1) return;

    if (res.error) {
      this.messages[idx] = { role: 'aria', content: `Error: ${res.error}`, timestamp: new Date().toISOString() };
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
    this.newConversation();
  }

  formatTime(iso: string): string {
    try {
      return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    } catch { return ''; }
  }

  formatDate(ts: number): string {
    if (!ts) return '';
    try {
      const d = new Date(ts * 1000);
      const now = new Date();
      if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
      }
      return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
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
