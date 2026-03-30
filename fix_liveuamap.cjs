const fs = require('fs');
const f = 'apis/sources/liveuamap.mjs';
let c = fs.readFileSync(f, 'utf8');

// Fix the base URL
c = c.replace(
  "const BASE_URL = 'https://api.liveuamap.com';",
  "const BASE_URL = 'https://a.liveuamap.com/api';"
);

// Fix the endpoint path - try both formats
c = c.replace(
  "const res = await fetch(`${BASE_URL}/v1/events/${region.id}?${params}`, {",
  "const res = await fetch(`${BASE_URL}/v1/events/${region.id}?${params}`, {"
);

// Also try without /v1 prefix as fallback
c = c.replace(
  "const res = await fetch(`${BASE_URL}/v1/events/${region.id}?${params}`, {",
  "const url = `${BASE_URL}/v1/events/${region.id}?${params}`;\n    const res = await fetch(url, {"
);

fs.writeFileSync(f, c);
console.log('Base URL fixed to https://a.liveuamap.com/api');
