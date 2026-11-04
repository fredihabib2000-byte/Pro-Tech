self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

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
    self.registration.showNotification(title, opts).then(() =>
      self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
        list.forEach(c => c.postMessage({ type: 'PLAY_ORDER_SOUND', data }));
      })
    )
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = event.notification.data?.url || '/admin.html';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const c of list) {
        if (c.url.includes('admin') && 'focus' in c) return c.focus();
      }
      return clients.openWindow(url);
    })
  );
});
