// Production environment — served by Express at /admin, API on same host
export const environment = {
  production: true,
  apiBase: '',          // same host:port as Express
  sseUrl: '/events',
  appName: 'ARKMURUS Intelligence',
  version: '2.0.0',
};
