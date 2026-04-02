import {
  Component, OnInit, OnDestroy, ViewChild, ElementRef,
  AfterViewChecked, ChangeDetectorRef
} from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { ChatService, ChatMessage, ChatUser, ConversationSummary } from '../../services/chat.service';
import { AuthService } from '../../services/auth.service';

@Component({
  selector: 'app-chat',
  templateUrl: './chat.component.html',
  styleUrls: ['./chat.component.scss']
})
export class ChatComponent implements OnInit, OnDestroy, AfterViewChecked {
  @ViewChild('messageList') messageListRef!: ElementRef;

  myId = '';
  myUsername = '';

  // Contact / conversation lists
  allUsers: ChatUser[] = [];
  conversations: ConversationSummary[] = [];

  // Active conversation
  activeUser: ChatUser | null = null;
  messages: ChatMessage[] = [];
  messageText = '';

  // UI state
  loadingMessages = false;
  isTyping = false;
  typingTimeout: any;
  remoteTyping = false;
  searchQuery = '';
  mobileShowConversation = false;

  private subs = new Subscription();
  private shouldScroll = false;

  constructor(
    private chat: ChatService,
    private auth: AuthService,
    private route: ActivatedRoute,
    private router: Router,
    private cdr: ChangeDetectorRef
  ) {}

  ngOnInit(): void {
    const user = this.auth.getCurrentUser();
    this.myId = user?.id || '';
    this.myUsername = user?.username || '';

    this.chat.connect();

    this.subs.add(
      this.chat.newMessage$.subscribe(msg => {
        if (
          this.activeUser &&
          (msg.from === this.activeUser.id || msg.to === this.activeUser.id) &&
          (msg.from === this.myId || msg.to === this.myId)
        ) {
          // Avoid duplicate if echoed back to sender
          if (!this.messages.find(m => m.id === msg.id)) {
            this.messages = [...this.messages, msg];
            this.shouldScroll = true;
          }
          if (msg.from === this.activeUser.id) {
            this.chat.emitMarkRead(this.activeUser.id);
          }
        }
        this.loadConversations();
        this.cdr.detectChanges();
      })
    );

    this.subs.add(
      this.chat.typing$.subscribe(({ fromId, typing }) => {
        if (fromId === this.activeUser?.id) {
          this.remoteTyping = typing;
          this.cdr.detectChanges();
        }
      })
    );

    this.loadUsers();
    this.loadConversations();

    // Open specific user from route
    this.subs.add(
      this.route.params.subscribe(params => {
        if (params['userId']) {
          this.openUserById(params['userId']);
        }
      })
    );
  }

  ngAfterViewChecked(): void {
    if (this.shouldScroll) {
      this.scrollToBottom();
      this.shouldScroll = false;
    }
  }

  loadUsers(): void {
    this.chat.getUsers().subscribe({
      next: (users) => { this.allUsers = users; },
      error: () => {}
    });
  }

  loadConversations(): void {
    this.chat.getConversations().subscribe({
      next: (convs) => { this.conversations = convs; },
      error: () => {}
    });
  }

  openConversation(summary: ConversationSummary): void {
    const user = this.allUsers.find(u => u.id === summary.userId);
    if (user) {
      this.openUser(user);
    } else {
      // user might not be in allUsers yet, create minimal object
      this.openUser({
        id: summary.userId,
        username: summary.username,
        fullName: summary.fullName,
        role: summary.role
      });
    }
  }

  openUserById(userId: string): void {
    this.chat.getUsers().subscribe({
      next: (users) => {
        this.allUsers = users;
        const user = users.find(u => u.id === userId);
        if (user) this.openUser(user);
      }
    });
  }

  openUser(user: ChatUser): void {
    this.activeUser = user;
    this.messages = [];
    this.remoteTyping = false;
    this.mobileShowConversation = true;
    this.loadMessages(user.id);
    this.router.navigate(['/chat', user.id], { replaceUrl: true });
  }

  loadMessages(userId: string): void {
    this.loadingMessages = true;
    this.chat.getMessages(userId).subscribe({
      next: (msgs) => {
        this.messages = msgs;
        this.loadingMessages = false;
        this.shouldScroll = true;
        this.chat.emitMarkRead(userId);
        this.loadConversations();
        this.cdr.detectChanges();
      },
      error: () => { this.loadingMessages = false; }
    });
  }

  sendMessage(): void {
    if (!this.messageText.trim() || !this.activeUser) return;
    const text = this.messageText.trim();
    this.messageText = '';
    this.stopTyping();
    this.chat.sendMessage(this.activeUser.id, text);
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.sendMessage();
    }
  }

  onInputChange(): void {
    if (!this.activeUser) return;
    if (!this.isTyping) {
      this.isTyping = true;
      this.chat.sendTyping(this.activeUser.id, true);
    }
    clearTimeout(this.typingTimeout);
    this.typingTimeout = setTimeout(() => this.stopTyping(), 2000);
  }

  stopTyping(): void {
    if (this.isTyping && this.activeUser) {
      this.isTyping = false;
      this.chat.sendTyping(this.activeUser.id, false);
    }
    clearTimeout(this.typingTimeout);
  }

  isOnline(userId: string): boolean {
    return this.chat.isOnline(userId);
  }

  isMine(msg: ChatMessage): boolean {
    return msg.from === this.myId;
  }

  formatTime(ts: string): string {
    const d = new Date(ts);
    const now = new Date();
    if (d.toDateString() === now.toDateString()) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) +
           ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  get filteredUsers(): ChatUser[] {
    const q = this.searchQuery.toLowerCase();
    if (!q) return this.allUsers;
    return this.allUsers.filter(u =>
      u.fullName.toLowerCase().includes(q) ||
      u.username.toLowerCase().includes(q)
    );
  }

  get totalUnread(): number {
    return this.conversations.reduce((sum, c) => sum + c.unread, 0);
  }

  backToList(): void {
    this.mobileShowConversation = false;
    this.activeUser = null;
    this.router.navigate(['/chat'], { replaceUrl: true });
  }

  private scrollToBottom(): void {
    try {
      const el = this.messageListRef?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    } catch {}
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
    this.stopTyping();
  }
}
