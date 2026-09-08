import { Injectable } from '@angular/core';

export interface KeyPairResult {
  publicKey: string;
  encryptedPrivateKey?: string;
}

/**
 * Browser Web Crypto ECDH (P-256) + AES-GCM.
 * Private keys are encrypted on the client with an account-derived key so they sync across sessions and incognito windows.
 */
@Injectable({
  providedIn: 'root'
})
export class CryptoService {
  private readonly algo = { name: 'ECDH', namedCurve: 'P-256' } as const;
  private privateKey: CryptoKey | null = null;
  private publicKeyB64: string | null = null;

  private storageKey(userId: number): string {
    return `chatconnect_e2e_priv_${userId}`;
  }

  private async deriveUserWrapKey(username: string): Promise<CryptoKey> {
    const enc = new TextEncoder();
    const rawKey = await crypto.subtle.importKey(
      'raw',
      enc.encode(username.toLowerCase()),
      { name: 'PBKDF2' },
      false,
      ['deriveKey']
    );
    return crypto.subtle.deriveKey(
      {
        name: 'PBKDF2',
        salt: enc.encode(`chatconnect_salt_${username.toLowerCase()}`),
        iterations: 100000,
        hash: 'SHA-256'
      },
      rawKey,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt']
    );
  }

  async ensureKeyPair(userId: number, username: string, serverEncryptedPrivKey?: string | null): Promise<KeyPairResult> {
    const stored = localStorage.getItem(this.storageKey(userId));
    if (stored) {
      try {
        const { publicKey, privateKey } = JSON.parse(stored) as {
          publicKey: string;
          privateKey: string;
        };
        this.privateKey = await crypto.subtle.importKey(
          'pkcs8',
          this.b64ToBuffer(privateKey),
          this.algo,
          true,
          ['deriveBits']
        );
        this.publicKeyB64 = publicKey;
        return { publicKey };
      } catch {
        localStorage.removeItem(this.storageKey(userId));
      }
    }

    // If server has an encrypted private key (e.g. Incognito window or new browser)
    if (serverEncryptedPrivKey) {
      try {
        const wrapKey = await this.deriveUserWrapKey(username);
        const parts = serverEncryptedPrivKey.split(':');
        if (parts.length === 3) {
          const iv = this.b64ToBuffer(parts[1]);
          const cipher = this.b64ToBuffer(parts[2]);
          const plainBytes = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, wrapKey, cipher);
          this.privateKey = await crypto.subtle.importKey(
            'pkcs8',
            plainBytes,
            this.algo,
            true,
            ['deriveBits']
          );
          const publicRaw = await crypto.subtle.exportKey('spki', (await this.derivePublicKeyFromPrivate(this.privateKey)));
          const pubB64 = this.bytesToB64(publicRaw);
          this.publicKeyB64 = pubB64;
          localStorage.setItem(this.storageKey(userId), JSON.stringify({
            publicKey: pubB64,
            privateKey: this.bytesToB64(plainBytes)
          }));
          return { publicKey: pubB64 };
        }
      } catch (err) {
        console.warn('Could not decrypt server key pair, generating new one:', err);
      }
    }

    // Generate new key pair
    const pair = await crypto.subtle.generateKey(this.algo, true, ['deriveBits']);
    const publicRaw = await crypto.subtle.exportKey('spki', pair.publicKey);
    const privateRaw = await crypto.subtle.exportKey('pkcs8', pair.privateKey);
    const publicKey = this.bytesToB64(publicRaw);
    const privateKey = this.bytesToB64(privateRaw);

    // Encrypt private key with account key for server backup
    const wrapKey = await this.deriveUserWrapKey(username);
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const cipherBuf = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, wrapKey, privateRaw);
    const encryptedPrivateKey = `v1:${this.bytesToB64(iv.buffer)}:${this.bytesToB64(cipherBuf)}`;

    localStorage.setItem(this.storageKey(userId), JSON.stringify({ publicKey, privateKey }));
    this.privateKey = pair.privateKey;
    this.publicKeyB64 = publicKey;
    return { publicKey, encryptedPrivateKey };
  }

  private async derivePublicKeyFromPrivate(privateKey: CryptoKey): Promise<CryptoKey> {
    const jwk = await crypto.subtle.exportKey('jwk', privateKey);
    const pubJwk: JsonWebKey = {
      kty: jwk.kty,
      crv: jwk.crv,
      x: jwk.x,
      y: jwk.y,
      ext: true
    };
    return crypto.subtle.importKey('jwk', pubJwk, this.algo, true, []);
  }

  hasLocalPrivateKey(): boolean {
    return !!this.privateKey;
  }

  getPublicKey(): string | null {
    return this.publicKeyB64;
  }

  async encryptText(peerPublicKeyB64: string, plaintext: string): Promise<string> {
    const key = await this.deriveAesKey(peerPublicKeyB64);
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const cipher = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv },
      key,
      new TextEncoder().encode(plaintext)
    );
    return `v1:${this.bytesToB64(iv.buffer)}:${this.bytesToB64(cipher)}`;
  }

  async decryptText(peerPublicKeyB64: string, payload: string): Promise<string> {
    if (!payload || !payload.startsWith('v1:')) {
      return payload || '';
    }
    const parts = payload.split(':');
    if (parts.length !== 3) {
      return payload;
    }
    const key = await this.deriveAesKey(peerPublicKeyB64);
    const iv = this.b64ToBuffer(parts[1]);
    const cipher = this.b64ToBuffer(parts[2]);
    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, cipher);
    return new TextDecoder().decode(plain);
  }

  async encryptBytes(peerPublicKeyB64: string, data: ArrayBuffer): Promise<ArrayBuffer> {
    const key = await this.deriveAesKey(peerPublicKeyB64);
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const cipher = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, data));
    const out = new Uint8Array(12 + cipher.byteLength);
    out.set(iv, 0);
    out.set(cipher, 12);
    return out.buffer;
  }

  async decryptBytes(peerPublicKeyB64: string, data: ArrayBuffer): Promise<ArrayBuffer> {
    const bytes = new Uint8Array(data);
    if (bytes.byteLength < 13) {
      throw new Error('Invalid encrypted file');
    }
    const key = await this.deriveAesKey(peerPublicKeyB64);
    const iv = bytes.slice(0, 12);
    const cipher = bytes.slice(12);
    return crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, cipher);
  }

  private async deriveAesKey(peerPublicKeyB64: string): Promise<CryptoKey> {
    if (!this.privateKey) {
      throw new Error('Local private key is not loaded');
    }
    const peerKey = await crypto.subtle.importKey(
      'spki',
      this.b64ToBuffer(peerPublicKeyB64),
      this.algo,
      false,
      []
    );
    const bits = await crypto.subtle.deriveBits(
      { name: 'ECDH', public: peerKey },
      this.privateKey,
      256
    );
    return crypto.subtle.importKey('raw', bits, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
  }

  private bytesToB64(buffer: ArrayBuffer): string {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
  }

  private b64ToBuffer(b64: string): ArrayBuffer {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer as ArrayBuffer;
  }
}
