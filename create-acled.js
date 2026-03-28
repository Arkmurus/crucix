const fs = require('fs');
const lines = [
"export const id = 'acled';",
"export const name = 'ACLED Conflict Data';",
"const TOKEN_URL = 'https://acleddata.com/oauth/token';",
"const API_URL = 'https://acleddata.com/api/acled/read';",
"let cachedToken = null;",
"let tokenExpiry = 0;",
"async function getToken(email, password) {",
"  if (cachedToken && Date.now() < tokenExpiry - 300000) return cachedToken;",
"  const body = new URLSearchParams({username: email, password: password, grant_type: 'password', client_id: 'acled'});",
"  const res = await globalThis.fetch(TOKEN_URL, {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: body.toString()});",
"  if (!res.ok) throw new Error('ACLED auth failed: HTTP ' + res.status);",
"  const data = await res.json();",
"  cachedToken = data.access_token;",
"  tokenExpiry = Date.now() + (data.expires_in * 1000);",
"  return cachedToken;",
"}",
"export async function fetch(env) {",
"  const email = env.ACLED_EMAIL;",
"  const password = env.ACLED_PASSWORD;",
"  if (!email || !password) return {ok: false, error: 'ACLED_EMAIL and ACLED_PASSWORD not set'};",
"  try {",
"    const token = await getToken(email, password);",
"    const since = new Date(Date.now()-7*864e5).toISOString().split('T')[0];",
"    const now = new Date().toISOString().split('T')[0];",
"    const url = API_URL + '?_format=json&event_date=' + since + '|' + now + '&event_date_where=BETWEEN&limit=100&fields=event_id_cnty|event_date|event_type|country|fatalities';",
"    const res = await globalThis.fetch(url, {headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'}});",
"    if (!res.ok) return {ok: false, error: 'ACLED API error: HTTP ' + res.status};",
"    const data = await res.json();",
"    const events = data.data || [];",
"    let fatalities = 0;",
"    const countries = {};",
"    for (const e of events) {",
"      fatalities += parseInt(e.fatalities) || 0;",
"      const c = e.country || 'Unknown';",
"      countries[c] = (countries[c] || 0) + 1;",
"    }",
"    const top = Object.entries(countries).sort((a,b)=>b[1]-a[1]).slice(0,4).map(([c,n])=>c+' ('+n+')').join(', ');",
"    const total = events.length;",
"    const signals = [];",
"    if (fatalities > 100) signals.push({severity: 'FLASH', text: 'ACLED: ' + fatalities + ' fatalities in last 7 days across ' + total + ' events'});",
"    else signals.push({severity: total > 50 ? 'PRIORITY' : 'ROUTINE', text: 'ACLED: ' + total + ' events, ' + fatalities + ' fatalities. Top: ' + top});",
"    return {ok: true, summary: 'ACLED: ' + total + ' events, ' + fatalities + ' fatalities. Countries: ' + top, signals};",
"  } catch(e) { return {ok: false, error: e.message}; }",
"}"
];
fs.writeFileSync('apis/sources/acled.mjs', lines.join('\n'));
console.log('Done');
```

Press **Ctrl+S** to save, close Notepad, then run:
```
node create-acled.js
```

You should see `Done`. Then restart:
```
npm run dev Sonnet 4.6