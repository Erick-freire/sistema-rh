const CACHE_ESTATICO = 'sistema-rh-estatico-v1';
const CACHE_DINAMICO = 'sistema-rh-dinamico-v1';
const PAGINA_OFFLINE = '/offline/';

const RECURSOS_ESTATICOS = [
  PAGINA_OFFLINE,
  '/static/css/style.css',
  '/static/pwa/icon-192.png',
  '/static/pwa/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_ESTATICO).then((cache) => cache.addAll(RECURSOS_ESTATICOS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((chaves) =>
      Promise.all(
        chaves
          .filter((k) => k !== CACHE_ESTATICO && k !== CACHE_DINAMICO)
          .map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  if (url.pathname.startsWith('/static/') || url.pathname.startsWith('/media/')) {
    event.respondWith(
      caches.match(req).then((cached) => {
        const rede = fetch(req).then((resp) => {
          caches.open(CACHE_ESTATICO).then((c) => c.put(req, resp.clone()));
          return resp;
        });
        return cached || rede;
      })
    );
    return;
  }

  event.respondWith(
    fetch(req)
      .then((resp) => {
        caches.open(CACHE_DINAMICO).then((c) => c.put(req, resp.clone()));
        return resp;
      })
      .catch(() =>
        caches.match(req).then((cached) => cached || caches.match(PAGINA_OFFLINE))
      )
  );
});