// Service Worker — Trading Bot PWA
// Permet l'installation sur Android et le fonctionnement en tâche de fond

const CACHE_NAME = 'trading-bot-v2';
const ASSETS = [
  '/',
  '/static/manifest.json',
];

// Installation : mise en cache des assets statiques
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

// Activation : nettoyage des anciens caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Fetch : network first pour l'API, cache first pour les assets
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // API → toujours réseau (données temps réel)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(event.request).catch(() =>
      new Response(JSON.stringify({ error: 'offline' }), {
        headers: { 'Content-Type': 'application/json' }
      })
    ));
    return;
  }

  // Assets → cache first
  event.respondWith(
    caches.match(event.request).then(cached =>
      cached || fetch(event.request).then(response => {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        return response;
      })
    )
  );
});

// Background sync : maintient la connexion active
self.addEventListener('periodicsync', event => {
  if (event.tag === 'bot-status') {
    event.waitUntil(fetch('/api/data'));
  }
});
