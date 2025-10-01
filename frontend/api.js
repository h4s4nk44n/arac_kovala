import Constants from 'expo-constants';
import { Platform } from 'react-native';

// Resolve a usable base URL for device/emulator:
let cachedBaseUrl;

function computeCandidates() {
  const candidates = [];
  const fromEnv = process.env.EXPO_PUBLIC_API_BASE || Constants?.expoConfig?.extra?.apiBase;
  if (fromEnv) candidates.push(fromEnv);

  const expo = Constants?.expoConfig || Constants?.manifest2?.extra?.expoClient;
  const debuggerHost = expo?.hostUri || expo?.debuggerHost;
  if (debuggerHost) {
    const host = debuggerHost.split(':')[0];
    candidates.push(`http://${host}:3000`);
  }

  if (Platform.OS === 'android') candidates.push('http://10.0.2.2:3000');
  candidates.push('http://127.0.0.1:3000');

  return Array.from(new Set(candidates));
}

async function getBaseUrl() {
  if (cachedBaseUrl) return cachedBaseUrl;
  const candidates = computeCandidates();
  for (const base of candidates) {
    try {
      const res = await withTimeout(fetch(base + '/health'), 1500);
      if (res && res.ok) {
        cachedBaseUrl = base;
        return cachedBaseUrl;
      }
    } catch (_) {
      // try next candidate
    }
  }
  cachedBaseUrl = candidates[0] || (Platform.OS === 'android' ? 'http://10.0.2.2:3000' : 'http://127.0.0.1:3000');
  return cachedBaseUrl;
}

const TIMEOUT_MS = 10000;

function withTimeout(promise, ms = TIMEOUT_MS) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error('Request timeout')), ms)),
  ]);
}

async function http(method, path, body) {
  const base = await getBaseUrl();
  const url = base + path;
  console.log('[API]', method, url);
  const res = await withTimeout(fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  }));
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

export const Api = {
  health: () => http('GET', '/health'),

  listFilters: () => http('GET', '/filters'),
  createFilter: (data) => http('POST', '/filters', data),
  updateFilter: (id, data) => http('PUT', `/filters/${id}`, data),
  deleteFilter: (id) => http('DELETE', `/filters/${id}`),

  feed: () => http('GET', '/feed'),
  filterCars: (id) => http('GET', `/filters/${id}/cars`),
  
  // --- ADD THIS NEW FUNCTION ---
  registerPushToken: (data) => http('POST', '/register-push-token', data),
};