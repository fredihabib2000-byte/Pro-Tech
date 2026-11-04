self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

function _writeSound() {
  return new Promise(resolve => {
    try {
      const req = indexedDB.open('pt_push', 1);
      req.onupgradeneeded = e => e.target.result.createObjectStore('signals');
      req.onsuccess = e => {
        try {
          const tx = e.target.result.transaction('signals', 'readwrite');
          tx.objectStore('signals').put(Date.now(), 'playSound');
          tx.oncomplete = resolve;
          tx.onerror = resolve;
        } catch(_){ resolve(); }
      };
      req.onerror = resolve;
    } catch(_){ resolve(); }
  });
}

self.addEventListener('push', event => {
  let data = {};
  try { data = event.data.json(); } catch(e) {}

  const title = data.title || '🛒 New Order!';
  const opts = {
    body: data.body || 'A new order has been placed on Pro-Tech',
    icon: data.icon || '/images/products/nexera-nx-wlc02-wireless-charger.jpg',
    vibrate: [300, 100, 300, 100, 300, 100, 300],
    tag: 'order-' + (data.orderId || Date.now()),
    requireInteraction: true,
    actions: [{ action: 'open', title: '📋 Open Admin' }],
    data: { url: '/admin.html' }
  };

  event.waitUntil(
    Promise.all([
      self.registration.showNotification(title, opts),
      _writeSound(),
      self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
        list.forEach(c => c.postMessage({ type: 'PLAY_ORDER_SOUND', data }));
      })
    ])
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = (event.notification.data?.url || '/admin.html') + '?sound=1';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const c of list) {
        if (c.url.includes('admin') && 'focus' in c) return c.focus();
      }
      return clients.openWindow(url);
    })
  );
});
