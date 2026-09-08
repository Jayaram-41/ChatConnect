export interface CapacitorConfig {
  appId: string;
  appName: string;
  webDir: string;
  bundledWebRuntime?: boolean;
  server?: {
    androidScheme?: string;
    cleartext?: boolean;
    url?: string;
  };
}

const config: CapacitorConfig = {
  appId: 'com.chatconnect.app',
  appName: 'ChatConnect',
  webDir: 'dist/frontend/browser',
  server: {
    androidScheme: 'https',
    cleartext: true
  }
};

export default config;
