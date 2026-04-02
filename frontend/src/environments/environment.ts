// Development environment — Angular dev server proxies /api/* to Express on 3117
export const environment = {
  production: false,
  apiBase: '',          // empty = relative URLs, proxied to Express by proxy.conf.json
  sseUrl: '/events',
  appName: 'ARKMURUS Intelligence',
  version: '2.0.0',
};
