import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, tap } from 'rxjs';

export interface LastMessageSnippet {
  id: number;
  content: string;
  message_type: string;
  file_name?: string | null;
  sender_id: number;
  is_encrypted: boolean;
  created_at: string;
}

export interface User {
  id: number;
  username: string;
  created_at?: string;
  public_key?: string | null;
  encrypted_private_key?: string | null;
  last_message?: LastMessageSnippet | null;
  last_message_at?: string | null;
  conversation_id?: number | null;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

import { getBackendHost } from './chat.service';

@Injectable({
  providedIn: 'root'
})
export class AuthService {
  get apiUrl(): string {
    const host = getBackendHost();
    const protocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'https:' : 'http:';
    return `${protocol}//${host}/api/auth`;
  }
  private tokenKey = 'chatconnect_token';
  private userKey = 'chatconnect_user';

  constructor(private http: HttpClient) {}

  login(credentials: { username: string; password: string }): Observable<AuthResponse> {
    return this.http.post<AuthResponse>(`${this.apiUrl}/login`, credentials).pipe(
      tap(response => {
        this.saveAuth(response);
      })
    );
  }

  register(userData: { username: string; password: string }): Observable<AuthResponse> {
    return this.http.post<AuthResponse>(`${this.apiUrl}/register`, userData).pipe(
      tap(response => {
        this.saveAuth(response);
      })
    );
  }

  private saveAuth(response: AuthResponse): void {
    if (response?.access_token) {
      localStorage.setItem(this.tokenKey, response.access_token);
    }
    if (response?.user) {
      localStorage.setItem(this.userKey, JSON.stringify(response.user));
    }
  }

  getToken(): string | null {
    return localStorage.getItem(this.tokenKey);
  }

  getCurrentUser(): User | null {
    const userStr = localStorage.getItem(this.userKey);
    return userStr ? JSON.parse(userStr) : null;
  }

  isLoggedIn(): boolean {
    return !!this.getToken();
  }

  logout(): void {
    localStorage.removeItem(this.tokenKey);
    localStorage.removeItem(this.userKey);
  }

  updateKeys(publicKey: string, encryptedPrivateKey?: string): Observable<User> {
    return this.http.put<User>(
      `${this.apiUrl}/public-key`,
      { public_key: publicKey, encrypted_private_key: encryptedPrivateKey },
      {
        headers: {
          Authorization: `Bearer ${this.getToken()}`
        }
      }
    ).pipe(
      tap(user => {
        localStorage.setItem(this.userKey, JSON.stringify(user));
      })
    );
  }

  updatePublicKey(publicKey: string): Observable<User> {
    return this.updateKeys(publicKey);
  }
}
