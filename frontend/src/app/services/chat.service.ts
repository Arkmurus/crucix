import { Injectable, OnDestroy } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { BehaviorSubject, Observable, Subject } from 'rxjs';
import { io, Socket } from 'socket.io-client';
import { AuthService } from './auth.service';

export interface ChatMessage {
  id: string;
  from: string;
  to: string;
  text: string;
  ts: string;
  read: boolean;
  fromUsername?: string;
  fromFullName?: string;
}

export interface ChatUser {
  id: string;
  username: string;
  fullName: string;
  role: string;
}

export interface ConversationSummary {
  userId: string;
  username: string;
  fullName: string;
  role: string;
  lastMessage: ChatMessage;
  unread: number;
}

@Injectable({ providedIn: 'root' })
export class ChatService implements OnDestroy {
  private socket: Socket | null = null;

  private onlineUsersSubject = new BehaviorSubject<string[]>([]);
  onlineUsers$ = this.onlineUsersSubject.asObservable();

  private newMessageSubject = new Subject<ChatMessage>();
  newMessage$ = this.newMessageSubject.asObservable();

  private typingSubject = new Subject<{ fromId: string; typing: boolean }>();
  typing$ = this.typingSubject.asObservable();

  private unreadCountSubject = new BehaviorSubject<number>(0);
  unreadCount$ = this.unreadCountSubject.asObservable();

  constructor(private http: HttpClient, private authService: AuthService) {}

  connect(): void {
    if (this.socket?.connected) return;
    const token = this.authService.getToken();
    if (!token) return;

    this.socket = io('/', {
      auth: { token },
      transports: ['websocket', 'polling']
    });

    this.socket.on('connect', () => {
      this.refreshUnread();
    });

    this.socket.on('online_users', (users: string[]) => {
      this.onlineUsersSubject.next(users);
    });

    this.socket.on('presence', ({ userId, online }: { userId: string; online: boolean }) => {
      const current = this.onlineUsersSubject.value;
      if (online && !current.includes(userId)) {
        this.onlineUsersSubject.next([...current, userId]);
      } else if (!online) {
        this.onlineUsersSubject.next(current.filter(id => id !== userId));
      }
    });

    this.socket.on('new_message', (msg: ChatMessage) => {
      this.newMessageSubject.next(msg);
      this.refreshUnread();
    });

    this.socket.on('typing', (data: { fromId: string; typing: boolean }) => {
      this.typingSubject.next(data);
    });
  }

  disconnect(): void {
    this.socket?.disconnect();
    this.socket = null;
  }

  isOnline(userId: string): boolean {
    return this.onlineUsersSubject.value.includes(userId);
  }

  sendMessage(toId: string, text: string): void {
    this.socket?.emit('send_message', { toId, text });
  }

  sendTyping(toId: string, typing: boolean): void {
    this.socket?.emit('typing', { toId, typing });
  }

  emitMarkRead(fromId: string): void {
    this.socket?.emit('mark_read', { fromId });
  }

  getUsers(): Observable<ChatUser[]> {
    return this.http.get<ChatUser[]>('/api/chat/users');
  }

  getConversations(): Observable<ConversationSummary[]> {
    return this.http.get<ConversationSummary[]>('/api/chat/conversations');
  }

  getMessages(userId: string): Observable<ChatMessage[]> {
    return this.http.get<ChatMessage[]>(`/api/chat/messages/${userId}`);
  }

  refreshUnread(): void {
    this.http.get<{ count: number }>('/api/chat/unread').subscribe({
      next: (r) => this.unreadCountSubject.next(r.count),
      error: () => {}
    });
  }

  ngOnDestroy(): void {
    this.disconnect();
  }
}
