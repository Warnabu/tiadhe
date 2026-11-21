// Service Worker para Guaguas FTV - PWA con cache offline
const CACHE_NAME = 'guaguas-ftv-v1';
const STATIC_ASSETS = [
  '/',
  '/cliente.js',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js',
  'https://tile.openstreetmap.org/'
];

// Install - cache static assets
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// Activate - clean old caches
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
    ))
  );
  self.clients.claim();
});

// Fetch - network first for API, cache first for static
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  
  // API calls - network first, fallback to cache
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(
      fetch(e.request).then(r => {
        const clone = r.clone();
        caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
        return r;
      }).catch(() => caches.match(e.request))
    );
    return;
  }
  
  // Static assets - cache first
  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(r => {
        if (r.ok) {
          const clone = r.clone();
          caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
        }
        return r;
      });
    })
  );
});

// Background sync for offline "me he subido" (future enhancement)
self.addEventListener('sync', e => {
  if (e.tag === 'subida-offline') {
    e.waitUntil(syncSubidas());
  }
});

async function syncSubidas() {
  // Implementation for when user taps "me he subido" offline
  // Would read from IndexedDB and send when online
}