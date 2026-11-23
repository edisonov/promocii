/* Промоции в магазините — service worker */
var CACHE = 'promo-shell-v1';
var SHELL = ['./', './index.html', './manifest.webmanifest',
  './icon-192.png', './icon-512.png', './icon-maskable-512.png', './apple-touch-icon.png'];

self.addEventListener('install', function(e){
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then(function(c){ return c.addAll(SHELL).catch(function(){}); }));
});

self.addEventListener('activate', function(e){
  e.waitUntil(
    caches.keys().then(function(keys){
      return Promise.all(keys.filter(function(k){ return k !== CACHE; }).map(function(k){ return caches.delete(k); }));
    }).then(function(){ return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function(e){
  var req = e.request;
  if(req.method !== 'GET') return;
  var url;
  try{ url = new URL(req.url); }catch(err){ return; }
  var sameOrigin = url.origin === self.location.origin;

  // Promotions data: network-first (fresh when online), cache fallback offline.
  if(sameOrigin && url.pathname.indexOf('data.json') !== -1){
    e.respondWith(
      fetch(req).then(function(r){
        var cp = r.clone();
        caches.open(CACHE).then(function(c){ c.put('./data.json', cp).catch(function(){}); });
        return r;
      }).catch(function(){ return caches.match('./data.json'); })
    );
    return;
  }

  // App navigation: network-first, cached shell offline.
  if(req.mode === 'navigate'){
    e.respondWith(
      fetch(req).then(function(r){
        var cp = r.clone();
        caches.open(CACHE).then(function(c){ c.put('./index.html', cp).catch(function(){}); });
        return r;
      }).catch(function(){ return caches.match('./index.html').then(function(m){ return m || caches.match('./'); }); })
    );
    return;
  }

  // Static same-origin assets: cache-first.
  if(sameOrigin && /\.(png|webmanifest|css|js|svg|woff2?)$/.test(url.pathname)){
    e.respondWith(
      caches.match(req).then(function(m){
        return m || fetch(req).then(function(r){
          var cp = r.clone();
          caches.open(CACHE).then(function(c){ c.put(req, cp).catch(function(){}); });
          return r;
        });
      })
    );
    return;
  }
  // Fonts CDN etc.: passthrough.
});
