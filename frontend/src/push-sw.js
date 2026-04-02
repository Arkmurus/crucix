// push-sw.js — Arkmurus Intelligence Platform push notification service worker
// Registered by the Angular app. Handles push events and notification clicks.

const ICON  = '/assets/images/app/favicon-32x32.png';
const BADGE = '/assets/images/app/favicon-32x32.png';

// ── Install ───────────────────────────────────────────────────────────────────

self.addEventListener('install', (event) => {
  console.log('[PushSW] Installing service worker');
  self.skipWaiting();
});

// ── Activate ──────────────────────────────────────────────────────────────────

self.addEventListener('activate', (event) => {
  console.log('[PushSW] Service worker activated');
  event.waitUntil(self.clients.claim());
});

// ── Push ──────────────────────────────────────────────────────────────────────

self.addEventListener('push', (event) => {
  let data = {};

  try {
    if (event.data) {
      data = event.data.json();
    }
  } catch (err) {
    console.warn('[PushSW] Malformed push payload:', err.message);
    data = {
      title: 'Arkmurus Intelligence Alert',
      body: event.data ? event.data.text() : 'New intelligence update available.',
    };
  }

  const title   = data.title   || 'Arkmurus Intelligence';
  const body    = data.body    || 'New intelligence update available.';
  const icon    = data.icon    || ICON;
  const badge   = data.badge   || BADGE;
  const url     = data.data?.url || '/';
  const actions = data.actions || [
    { action: 'open',    title: 'View'    },
    { action: 'dismiss', title: 'Dismiss' },
  ];

  const options = {
    body,
    icon,
    badge,
    data: { url },
    actions,
    requireInteraction: false,
    tag: 'arkmurus-intel',
    renotify: true,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// ── Notification click ────────────────────────────────────────────────────────

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  if (event.action === 'dismiss') return;

  const targetUrl = event.notification.data?.url || '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      // Focus an existing window on the target URL if one is open
      for (const client of clients) {
        const clientUrl = new URL(client.url);
        const target    = new URL(targetUrl, self.location.origin);
        if (clientUrl.pathname === target.pathname && 'focus' in client) {
          return client.focus();
        }
      }
      // Otherwise open a new window
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});
