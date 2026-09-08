import { Injectable, OnDestroy } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, Subject, BehaviorSubject } from 'rxjs';
import { AuthService, User } from './auth.service';

export interface ConversationParticipant {
  id: number;
  user_id: number;
  joined_at: string;
  user: User;
}

export interface Conversation {
  id: number;
  created_at: string;
  participants: ConversationParticipant[];
  lastMessage?: string;
  unreadCount?: number;
}

export interface Message {
  id: number;
  conversation_id: number;
  sender_id: number;
  content: string;
  message_type?: string;
  file_url?: string | null;
  file_name?: string | null;
  file_size?: number | null;
  is_encrypted?: boolean;
  created_at: string;
  sender?: User;
  decryptedContent?: string;
  attachmentObjectUrl?: string;
}

export interface SendMessagePayload {
  content?: string;
  message_type?: string;
  file_url?: string | null;
  file_name?: string | null;
  file_size?: number | null;
  is_encrypted?: boolean;
}

export interface UploadResult {
  file_url: string;
  file_name: string;
  file_size: number;
  content_type: string | null;
}

export function getBackendHost(): string {
  if (typeof window !== 'undefined') {
    const saved = localStorage.getItem('chatconnect_backend_host');
    if (saved) return saved;
    const hostname = window.location.hostname;
    if (hostname && hostname !== 'localhost' && hostname !== '127.0.0.1') {
      return `${hostname}:8000`;
    }
  }
  return '127.0.0.1:8000';
}

@Injectable({
  providedIn: 'root'
})
export class ChatService implements OnDestroy {
  get baseUrl(): string {
    const host = getBackendHost();
    const protocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'https:' : 'http:';
    return `${protocol}//${host}`;
  }

  get apiUrl(): string {
    return `${this.baseUrl}/api`;
  }

  get filesBaseUrl(): string {
    return this.baseUrl;
  }

  get wsUrl(): string {
    const host = getBackendHost();
    const protocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${host}/ws`;
  }

  private socket: WebSocket | null = null;
  private messageSubject = new Subject<Message>();
  public onNewMessage$ = this.messageSubject.asObservable();

  private isConnectedSubject = new BehaviorSubject<boolean>(false);
  public isConnected$ = this.isConnectedSubject.asObservable();

  private pingInterval: any = null;

  constructor(
    private http: HttpClient,
    private authService: AuthService
  ) {}

  private getAuthHeaders(): HttpHeaders {
    const token = this.authService.getToken();
    return new HttpHeaders({
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    });
  }

  private getTokenHeaders(): HttpHeaders {
    const token = this.authService.getToken();
    return new HttpHeaders({
      'Authorization': `Bearer ${token}`
    });
  }

  // ===================== REST APIs =====================

  getUsers(): Observable<User[]> {
    return this.http.get<User[]>(`${this.apiUrl}/auth/users`, {
      headers: this.getAuthHeaders()
    });
  }

  getConversations(): Observable<Conversation[]> {
    return this.http.get<Conversation[]>(`${this.apiUrl}/conversations`, {
      headers: this.getAuthHeaders()
    });
  }

  getOrCreateConversation(targetUserId: number): Observable<Conversation> {
    return this.http.post<Conversation>(
      `${this.apiUrl}/conversations`,
      { target_user_id: targetUserId },
      { headers: this.getAuthHeaders() }
    );
  }

  getMessages(conversationId: number): Observable<Message[]> {
    return this.http.get<Message[]>(
      `${this.apiUrl}/conversations/${conversationId}/messages`,
      { headers: this.getAuthHeaders() }
    );
  }

  sendMessage(conversationId: number, payload: SendMessagePayload): Observable<Message> {
    return this.http.post<Message>(
      `${this.apiUrl}/conversations/${conversationId}/messages`,
      {
        content: payload.content || '',
        message_type: payload.message_type || 'text',
        file_url: payload.file_url || null,
        file_name: payload.file_name || null,
        file_size: payload.file_size || null,
        is_encrypted: payload.is_encrypted || false
      },
      { headers: this.getAuthHeaders() }
    );
  }

  uploadFile(file: File): Observable<UploadResult> {
    const form = new FormData();
    form.append('file', file, file.name);
    return this.http.post<UploadResult>(
      `${this.apiUrl}/conversations/upload`,
      form,
      { headers: this.getTokenHeaders() }
    );
  }

  fileAbsoluteUrl(fileUrl: string): string {
    if (fileUrl.startsWith('http')) {
      return fileUrl;
    }
    return `${this.filesBaseUrl}${fileUrl}`;
  }

  // ===================== WebSocket =====================

  connectWebSocket(): void {
    const token = this.authService.getToken();
    if (!token) {
      console.warn('Cannot connect WebSocket: No token available');
      return;
    }

    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    try {
      this.socket = new WebSocket(`${this.wsUrl}?token=${token}`);

      this.socket.onopen = () => {
        this.isConnectedSubject.next(true);
        this.pingInterval = setInterval(() => {
          if (this.socket?.readyState === WebSocket.OPEN) {
            this.socket.send(JSON.stringify({ type: 'ping' }));
          }
        }, 25000);
      };

      this.socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'new_message' && data.message) {
            this.messageSubject.next(data.message);
          }
        } catch (e) {
          console.error('Error parsing WebSocket message:', e);
        }
      };

      this.socket.onclose = () => {
        this.isConnectedSubject.next(false);
        this.cleanupPing();
      };

      this.socket.onerror = (error) => {
        console.warn('WebSocket error:', error);
        this.isConnectedSubject.next(false);
      };
    } catch (err) {
      console.error('Failed to initiate WebSocket connection:', err);
    }
  }

  disconnectWebSocket(): void {
    this.cleanupPing();
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.isConnectedSubject.next(false);
  }

  private cleanupPing(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  ngOnDestroy(): void {
    this.disconnectWebSocket();
  }
}
