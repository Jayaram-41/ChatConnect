import { Component, OnInit, OnDestroy, ViewChild, ElementRef, AfterViewChecked } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { firstValueFrom, Subscription } from 'rxjs';
import { AuthService, User } from '../../services/auth.service';
import { ChatService, Conversation, Message } from '../../services/chat.service';
import { CryptoService } from '../../services/crypto.service';

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './chat.component.html',
  styleUrl: './chat.component.css'
})
export class ChatComponent implements OnInit, OnDestroy, AfterViewChecked {
  @ViewChild('messagesContainer') private messagesContainer!: ElementRef;
  @ViewChild('fileInput') private fileInput!: ElementRef<HTMLInputElement>;

  currentUser: User | null = null;
  users: User[] = [];
  filteredUsers: User[] = [];
  searchQuery: string = '';
  lastMessagePreviews: Record<number, string> = {};

  selectedUser: User | null = null;
  activeConversation: Conversation | null = null;
  messages: Message[] = [];

  newMessageContent: string = '';
  isLoadingUsers = false;
  isLoadingMessages = false;
  isSending = false;
  errorMessage = '';
  e2eReady = false;

  isWsConnected = false;

  private messageSub: Subscription | null = null;
  private wsConnectedSub: Subscription | null = null;
  private shouldScrollToBottom = false;

  constructor(
    private authService: AuthService,
    private chatService: ChatService,
    private cryptoService: CryptoService,
    private router: Router
  ) {}

  ngOnInit(): void {
    this.currentUser = this.authService.getCurrentUser();
    if (!this.currentUser) {
      this.router.navigate(['/login']);
      return;
    }

    this.chatService.connectWebSocket();

    this.wsConnectedSub = this.chatService.isConnected$.subscribe((connected: boolean) => {
      this.isWsConnected = connected;
    });

    this.messageSub = this.chatService.onNewMessage$.subscribe((incomingMessage: Message) => {
      this.handleIncomingMessage(incomingMessage);
    });

    this.bootstrapKeysAndUsers();
  }

  private async bootstrapKeysAndUsers(): Promise<void> {
    if (!this.currentUser) {
      return;
    }
    try {
      const keyResult = await this.cryptoService.ensureKeyPair(
        this.currentUser.id,
        this.currentUser.username,
        this.currentUser.encrypted_private_key
      );
      if (this.currentUser.public_key !== keyResult.publicKey || keyResult.encryptedPrivateKey) {
        const updated = await firstValueFrom(this.authService.updateKeys(keyResult.publicKey, keyResult.encryptedPrivateKey));
        this.currentUser = {
          ...this.currentUser,
          public_key: updated.public_key,
          encrypted_private_key: updated.encrypted_private_key
        };
      }
    } catch (err) {
      console.error('Failed to initialize E2E keys:', err);
    }
    this.loadUsers();
  }

  loadUsers(): void {
    this.isLoadingUsers = true;
    this.chatService.getUsers().subscribe({
      next: (users: User[]) => {
        this.isLoadingUsers = false;
        this.users = users;
        if (this.selectedUser) {
          const fresh = users.find(u => u.id === this.selectedUser?.id);
          if (fresh?.public_key && this.selectedUser && (!this.selectedUser.public_key || this.selectedUser.public_key !== fresh.public_key)) {
            this.selectedUser.public_key = fresh.public_key;
            this.e2eReady = !!(this.selectedUser.public_key && this.cryptoService.hasLocalPrivateKey());
            this.reDecryptPendingMessages();
          }
        }
        this.filterUsers();
        this.refreshLastMessagePreviews();
      },
      error: (err: any) => {
        this.isLoadingUsers = false;
        console.error('Failed to load users:', err);
      }
    });
  }

  filterUsers(): void {
    const q = this.searchQuery.trim().toLowerCase();
    if (!q) {
      // In Recent Chats, show only people who have messages/chats (or currently active chat)
      this.filteredUsers = this.users.filter(
        u => (u.last_message != null || u.last_message_at != null) || (this.selectedUser?.id === u.id)
      );
    } else {
      // In Search, allow searching across all registered users
      this.filteredUsers = this.users.filter(u => u.username.toLowerCase().includes(q));
    }
  }

  onSelectUser(user: User): void {
    if (this.selectedUser?.id === user.id && this.activeConversation) return;

    this.selectedUser = user;
    this.errorMessage = '';
    this.isLoadingMessages = true;
    this.messages = [];
    this.e2eReady = !!(user.public_key && this.cryptoService.hasLocalPrivateKey());

    this.chatService.getOrCreateConversation(user.id).subscribe({
      next: (conv: Conversation) => {
        this.activeConversation = conv;
        user.conversation_id = conv.id;
        this.loadMessages(conv.id);
        this.filterUsers();

        // Background refresh to catch public keys if counterpart just logged in
        this.chatService.getUsers().subscribe({
          next: (freshUsers) => {
            this.users = freshUsers;
            const updated = freshUsers.find(u => u.id === user.id);
            if (updated && this.selectedUser && this.selectedUser.id === user.id) {
              if (updated.public_key && (!this.selectedUser.public_key || this.selectedUser.public_key !== updated.public_key)) {
                this.selectedUser.public_key = updated.public_key;
                this.e2eReady = !!(this.selectedUser.public_key && this.cryptoService.hasLocalPrivateKey());
                this.reDecryptPendingMessages();
              }
            }
            this.refreshLastMessagePreviews();
          }
        });
      },
      error: (err: any) => {
        this.isLoadingMessages = false;
        this.errorMessage = 'Could not open conversation. Please try again.';
        console.error(err);
      }
    });
  }

  loadMessages(conversationId: number): void {
    this.chatService.getMessages(conversationId).subscribe({
      next: async (messages: Message[]) => {
        this.messages = await Promise.all(messages.map(m => this.prepareMessage(m)));
        this.isLoadingMessages = false;
        this.scrollToBottom();
      },
      error: (err: any) => {
        this.isLoadingMessages = false;
        console.error('Failed to load messages:', err);
      }
    });
  }

  sendMessage(): void {
    if (!this.activeConversation || !this.newMessageContent.trim() || this.isSending) {
      return;
    }

    const content = this.newMessageContent.trim();
    this.newMessageContent = '';
    this.sendPayload({ content, message_type: 'text' });
  }

  onAttachClick(): void {
    this.fileInput?.nativeElement.click();
  }

  async onFileSelected(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file || !this.activeConversation || this.isSending) {
      return;
    }
    if (file.size > 25 * 1024 * 1024) {
      this.errorMessage = 'File is too large (max 25MB).';
      return;
    }

    this.isSending = true;
    this.errorMessage = '';
    try {
      let uploadFile = file;
      let isEncrypted = false;
      const peerKey = this.selectedUser?.public_key;

      if (peerKey && this.cryptoService.hasLocalPrivateKey()) {
        const encrypted = await this.cryptoService.encryptBytes(peerKey, await file.arrayBuffer());
        uploadFile = new File([new Uint8Array(encrypted)], file.name, { type: 'application/octet-stream' });
        isEncrypted = true;
      }

      const uploaded = await firstValueFrom(this.chatService.uploadFile(uploadFile));
      const caption = this.newMessageContent.trim();
      this.newMessageContent = '';
      let content = caption;
      if (isEncrypted && peerKey) {
        content = await this.cryptoService.encryptText(peerKey, caption || file.name);
      }

      await this.sendPayloadAsync({
        content: content || (isEncrypted ? '' : file.name),
        message_type: this.detectMessageType(file),
        file_url: uploaded.file_url,
        file_name: file.name,
        file_size: file.size,
        is_encrypted: isEncrypted
      });
    } catch (err) {
      console.error(err);
      this.errorMessage = 'Failed to send attachment. Please retry.';
      this.isSending = false;
    }
  }

  private sendPayload(payload: {
    content: string;
    message_type: string;
    file_url?: string | null;
    file_name?: string | null;
    file_size?: number | null;
  }): void {
    this.sendPayloadAsync(payload).catch(err => {
      console.error(err);
      this.errorMessage = 'Failed to send message. Please retry.';
      this.isSending = false;
    });
  }

  private async sendPayloadAsync(payload: {
    content: string;
    message_type: string;
    file_url?: string | null;
    file_name?: string | null;
    file_size?: number | null;
    is_encrypted?: boolean;
  }): Promise<void> {
    if (!this.activeConversation) {
      return;
    }
    this.isSending = true;
    let content = payload.content;
    let isEncrypted = payload.is_encrypted ?? false;
    const peerKey = this.selectedUser?.public_key;

    if (!isEncrypted && payload.message_type === 'text' && peerKey && this.cryptoService.hasLocalPrivateKey()) {
      content = await this.cryptoService.encryptText(peerKey, content);
      isEncrypted = true;
    }

    const sentMsg = await firstValueFrom(this.chatService.sendMessage(this.activeConversation.id, {
      content,
      message_type: payload.message_type,
      file_url: payload.file_url,
      file_name: payload.file_name,
      file_size: payload.file_size,
      is_encrypted: isEncrypted
    }));

    this.isSending = false;
    const prepared = await this.prepareMessage(sentMsg);
    if (!this.messages.some(m => m.id === prepared.id)) {
      this.messages.push(prepared);
      this.scrollToBottom();
    }
    this.bumpUserFromMessage(prepared);
  }

  private async handleIncomingMessage(incoming: Message): Promise<void> {
    if (incoming.sender?.public_key) {
      const senderUser = this.users.find(u => u.id === incoming.sender_id);
      if (senderUser) {
        senderUser.public_key = incoming.sender.public_key;
      }
      if (this.selectedUser && this.selectedUser.id === incoming.sender_id) {
        this.selectedUser.public_key = incoming.sender.public_key;
        this.e2eReady = !!(this.selectedUser.public_key && this.cryptoService.hasLocalPrivateKey());
      }
    }

    const prepared = await this.prepareMessage(incoming);
    if (this.activeConversation && prepared.conversation_id === this.activeConversation.id) {
      if (!this.messages.some(m => m.id === prepared.id)) {
        this.messages.push(prepared);
        this.scrollToBottom();
      }
      this.reDecryptPendingMessages();
    }
    this.bumpUserFromMessage(prepared);
  }

  private async reDecryptPendingMessages(): Promise<void> {
    let changed = false;
    for (let i = 0; i < this.messages.length; i++) {
      const m = this.messages[i];
      if (m.is_encrypted && (m.decryptedContent === 'Encrypted message' || m.decryptedContent === 'Unable to decrypt this message.' || !m.decryptedContent)) {
        const rep = await this.prepareMessage(m);
        if (rep.decryptedContent && rep.decryptedContent !== 'Encrypted message' && rep.decryptedContent !== 'Unable to decrypt this message.') {
          this.messages[i] = rep;
          changed = true;
        }
      }
    }
    if (changed) {
      this.scrollToBottom();
    }
  }

  private bumpUserFromMessage(msg: Message): void {
    const otherId = msg.sender_id === this.currentUser?.id
      ? this.selectedUser?.id
      : msg.sender_id;

    const user = this.users.find(u =>
      u.id === otherId || u.conversation_id === msg.conversation_id
    );
    if (!user) {
      this.loadUsers();
      return;
    }

    user.last_message = {
      id: msg.id,
      content: msg.content,
      message_type: msg.message_type || 'text',
      file_name: msg.file_name,
      sender_id: msg.sender_id,
      is_encrypted: !!msg.is_encrypted,
      created_at: msg.created_at
    };
    user.last_message_at = msg.created_at;
    user.conversation_id = msg.conversation_id;
    this.lastMessagePreviews[user.id] = this.previewFromPrepared(msg);

    this.users.sort((a, b) => {
      if (a.last_message_at && b.last_message_at) {
        return new Date(b.last_message_at).getTime() - new Date(a.last_message_at).getTime();
      }
      if (a.last_message_at) return -1;
      if (b.last_message_at) return 1;
      return a.username.localeCompare(b.username);
    });
    this.filterUsers();
  }

  private async prepareMessage(msg: Message): Promise<Message> {
    const prepared: Message = { ...msg };
    const peer = this.peerPublicKeyFor(msg);

    if (msg.is_encrypted && peer) {
      try {
        if (msg.content) {
          prepared.decryptedContent = await this.cryptoService.decryptText(peer, msg.content);
        } else {
          prepared.decryptedContent = '';
        }
      } catch {
        prepared.decryptedContent = 'Unable to decrypt this message.';
      }
    } else if (msg.is_encrypted) {
      prepared.decryptedContent = 'Encrypted message';
    } else {
      prepared.decryptedContent = msg.content;
    }

    if (msg.file_url) {
      prepared.attachmentObjectUrl = await this.resolveAttachment(msg, peer);
    }
    return prepared;
  }

  private peerPublicKeyFor(msg: Message): string | null {
    if (msg.sender?.public_key && msg.sender_id !== this.currentUser?.id) {
      return msg.sender.public_key;
    }
    if (this.selectedUser?.public_key && msg.conversation_id === this.activeConversation?.id) {
      return this.selectedUser.public_key;
    }
    const targetId = msg.sender_id === this.currentUser?.id ? this.selectedUser?.id : msg.sender_id;
    const other = this.users.find(u => u.id === targetId || u.conversation_id === msg.conversation_id);
    return other?.public_key || msg.sender?.public_key || this.selectedUser?.public_key || null;
  }

  private async resolveAttachment(msg: Message, peerKey: string | null): Promise<string | undefined> {
    const abs = this.chatService.fileAbsoluteUrl(msg.file_url!);
    if (!msg.is_encrypted) {
      return abs;
    }
    if (!peerKey) {
      return undefined;
    }
    try {
      const res = await fetch(abs);
      const buf = await res.arrayBuffer();
      const decrypted = await this.cryptoService.decryptBytes(peerKey, buf);
      const blob = new Blob([new Uint8Array(decrypted)], { type: this.mimeFromName(msg.file_name || '', msg.message_type) });
      return URL.createObjectURL(blob);
    } catch (err) {
      console.warn('Failed to decrypt attachment', err);
      return undefined;
    }
  }

  private async refreshLastMessagePreviews(): Promise<void> {
    const next: Record<number, string> = {};
    for (const user of this.users) {
      next[user.id] = await this.computePreview(user);
    }
    this.lastMessagePreviews = next;
  }

  private async computePreview(user: User): Promise<string> {
    const last = user.last_message;
    if (!last) {
      return 'Click to chat';
    }
    if (last.message_type === 'image') return last.file_name ? `Photo · ${last.file_name}` : 'Photo';
    if (last.message_type === 'video') return last.file_name ? `Video · ${last.file_name}` : 'Video';
    if (last.message_type === 'file') return last.file_name ? `File · ${last.file_name}` : 'File';
    if (last.is_encrypted && user.public_key && this.cryptoService.hasLocalPrivateKey()) {
      try {
        const plain = await this.cryptoService.decryptText(user.public_key, last.content);
        return plain || 'Encrypted message';
      } catch {
        return 'Encrypted message';
      }
    }
    if (last.is_encrypted) {
      return 'Encrypted message';
    }
    return last.content || 'Message';
  }

  private previewFromPrepared(msg: Message): string {
    if (msg.message_type === 'image') return msg.file_name ? `Photo · ${msg.file_name}` : 'Photo';
    if (msg.message_type === 'video') return msg.file_name ? `Video · ${msg.file_name}` : 'Video';
    if (msg.message_type === 'file') return msg.file_name ? `File · ${msg.file_name}` : 'File';
    return msg.decryptedContent || msg.content || 'Message';
  }

  displayText(msg: Message): string {
    return msg.decryptedContent ?? msg.content ?? '';
  }

  detectMessageType(file: File): 'image' | 'video' | 'file' {
    if (file.type.startsWith('image/')) return 'image';
    if (file.type.startsWith('video/')) return 'video';
    const name = file.name.toLowerCase();
    if (/\.(png|jpe?g|gif|webp|bmp|svg)$/.test(name)) return 'image';
    if (/\.(mp4|webm|mov|mkv|avi)$/.test(name)) return 'video';
    return 'file';
  }

  mimeFromName(name: string, messageType?: string): string {
    const lower = name.toLowerCase();
    if (lower.endsWith('.png')) return 'image/png';
    if (lower.endsWith('.jpg') || lower.endsWith('.jpeg')) return 'image/jpeg';
    if (lower.endsWith('.gif')) return 'image/gif';
    if (lower.endsWith('.webp')) return 'image/webp';
    if (lower.endsWith('.mp4')) return 'video/mp4';
    if (lower.endsWith('.webm')) return 'video/webm';
    if (messageType === 'image') return 'image/*';
    if (messageType === 'video') return 'video/mp4';
    return 'application/octet-stream';
  }

  formatFileSize(bytes?: number | null): string {
    if (!bytes && bytes !== 0) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  private parseUtcDate(isoString?: string | null): Date | null {
    if (!isoString) return null;
    let s = String(isoString).trim();
    if (!s) return null;
    if (!s.endsWith('Z') && !s.includes('+') && !/[0-9]T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?-[0-9]{2}/.test(s)) {
      s += 'Z';
    }
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  formatSidebarTime(isoString?: string | null): string {
    const date = this.parseUtcDate(isoString);
    if (!date) return '';
    const now = new Date();
    const sameDay = date.toDateString() === now.toDateString();
    if (sameDay) {
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  formatTime(isoString: string): string {
    const date = this.parseUtcDate(isoString);
    if (!date) return '';
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  onKeyDown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.sendMessage();
    }
  }

  isMyMessage(message: Message): boolean {
    return message.sender_id === this.currentUser?.id;
  }

  getUserInitials(username: string): string {
    if (!username) return '?';
    return username.slice(0, 2).toUpperCase();
  }

  logout(): void {
    this.chatService.disconnectWebSocket();
    this.authService.logout();
    this.router.navigate(['/login']);
  }

  ngAfterViewChecked(): void {
    if (this.shouldScrollToBottom) {
      this.executeScrollToBottom();
      this.shouldScrollToBottom = false;
    }
  }

  scrollToBottom(): void {
    this.shouldScrollToBottom = true;
  }

  private executeScrollToBottom(): void {
    try {
      if (this.messagesContainer) {
        this.messagesContainer.nativeElement.scrollTop = this.messagesContainer.nativeElement.scrollHeight;
      }
    } catch (err) {
      console.warn('Scroll error:', err);
    }
  }

  ngOnDestroy(): void {
    for (const msg of this.messages) {
      if (msg.attachmentObjectUrl?.startsWith('blob:')) {
        URL.revokeObjectURL(msg.attachmentObjectUrl);
      }
    }
    if (this.messageSub) this.messageSub.unsubscribe();
    if (this.wsConnectedSub) this.wsConnectedSub.unsubscribe();
    this.chatService.disconnectWebSocket();
  }
}
