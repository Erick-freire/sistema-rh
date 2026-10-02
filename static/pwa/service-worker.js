const CACHE_ESTATICO = 'sistema-rh-estatico-v2';
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
          .filter((k) => k !== CACHE_ESTATICO)
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
    // Rede primeiro, cache só como reserva. Servir do cache primeiro fazia toda
    // mudança de CSS/JS aparecer apenas no carregamento seguinte: a tela vinha
    // com o estilo antigo e parecia quebrada. O arquivo não tem hash no nome
    // (`{% static %}` puro), então o cache não teria como saber que envelheceu.
    event.respondWith(
      fetch(req)
        .then((resp) => {
          if (resp.ok) {
            const copia = resp.clone();
            caches.open(CACHE_ESTATICO).then((c) => c.put(req, copia));
          }
          return resp;
        })
        // `ignoreSearch` porque a URL carrega ?v=<mtime>: sem isso a reserva
        // offline nunca casaria com o que foi guardado no install.
        .catch(() => caches.match(req, { ignoreSearch: true }))
    );
    return;
  }

  // Página HTML nunca vai para o cache. Ela sai do servidor amarrada a quem
  // pediu: traz o token CSRF do cookie daquele momento (devolver uma cópia
  // velha derruba o POST seguinte com 403 "CSRF token incorrect") e traz os
  // dados de quem estava logado (no aparelho compartilhado, a tela de um
  // apareceria para o próximo). Sem rede, mostra a página offline.
  event.respondWith(fetch(req).catch(() => caches.match(PAGINA_OFFLINE)));
});
