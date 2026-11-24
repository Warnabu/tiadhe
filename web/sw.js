// Service Worker de Guaguas FTV. Súbele la versión en cada despliegue que cambie assets.
const VERSION = 'v16-2026-11-24-paradas-por-sentido';
const CACHE = 'guaguas-ftv-' + VERSION;
const ICONOS = ['/icons/icon-192.png', '/icons/icon-512.png', '/manifest.webmanifest'];
const LEAFLET = ['https://unpkg.com/leaflet@1.9.4/dist/leaflet.css', 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'];
const SHELL = ['/', '/cliente.js'];   // network-first: siempre intentan la red para no quedarse con código viejo

self.addEventListener('install', e => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    // addAll falla entero si UNA petición falla: se añaden de una en una y se toleran errores.
    await Promise.all([...SHELL, ...ICONOS, ...LEAFLET].map(u => c.add(u).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);   // borra TODAS las caches antiguas
    await self.clients.claim();
  })());
});

self.addEventListener('message', e => { if (e.data === 'SKIP_WAITING') self.skipWaiting(); });

const redPrimero = async req => {
  try {
    const r = await fetch(req);
    if (r.ok) (await caches.open(CACHE)).put(req, r.clone());
    return r;
  } catch (err) {
    const c = await caches.match(req);
    if (c) return c;
    throw err;
  }
};
const cachePrimero = async req => {
  const c = await caches.match(req);
  if (c) return c;
  const r = await fetch(req);
  if (r.ok) (await caches.open(CACHE)).put(req, r.clone());
  return r;
};

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;                       // nunca interceptar POST (subidas, registro, tokens)
  const url = new URL(req.url);
  if (!url.protocol.startsWith('http')) return;
  if (url.origin !== location.origin) {                   // solo Leaflet de unpkg; Google/Firebase/gstatic/OSM pasan directos
    if (url.hostname === 'unpkg.com') e.respondWith(cachePrimero(req));
    return;
  }
  if (url.pathname.startsWith('/__/')) return;            // proxy de Firebase Auth: jamás cachear
  if (url.pathname === '/health') return;
  if (url.pathname.startsWith('/api/')) {                 // datos: red primero, caché solo como respaldo offline
    e.respondWith(redPrimero(req));
    return;
  }
  if (req.mode === 'navigate' || url.pathname === '/' || url.pathname.endsWith('.js') || url.pathname.endsWith('.html')) {
    e.respondWith(redPrimero(req));
    return;
  }
  e.respondWith(cachePrimero(req));                       // iconos, manifest…
});

// Notificaciones push (FCM). No usamos firebase-messaging-sw.js: este es el único SW del scope "/".
self.addEventListener('push', e => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (_) { data = { notification: { body: e.data && e.data.text() } }; }
  const n = data.notification || {};
  const d = data.data || {};
  e.waitUntil(self.registration.showNotification(n.title || d.title || 'Guaguas FTV', {
    body: n.body || d.body || 'Nueva notificación',
    icon: '/icons/icon-192.png', badge: '/icons/icon-192.png', data: d,
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil((async () => {
    const url = (e.notification.data && e.notification.data.url) || '/';   // p. ej. /?parada=… del aviso
    const lista = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of lista) if ('focus' in c) { if ('navigate' in c) await c.navigate(url).catch(() => {}); return c.focus(); }
    return clients.openWindow(url);
  })());
});
