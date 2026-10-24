const CACHE = 'rz-ai-shell-v1';
const SHELL = ['/', '/manifest.webmanifest', '/app-icon.svg'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/v1/') || url.pathname === '/mcp' || url.pathname === '/docs' || url.pathname.startsWith('/openapi')) return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).then(response => {
      if (response.ok && url.pathname === '/') caches.open(CACHE).then(cache => cache.put('/', response.clone()));
      return response;
    }).catch(async () => (await caches.match('/')) || Response.error()));
  }
});
