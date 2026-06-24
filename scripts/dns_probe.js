// DNS probe — test if IP-based connection works
const http = require('http');
const https = require('https');

function testIP() {
  return new Promise((resolve) => {
    const req = http.get('http://[fdaa:60:7499:a7b:494:61da:69a4:2]:8000/health/live', { timeout: 8000 }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve('IP OK: ' + d.slice(0, 80)));
    });
    req.on('error', e => resolve('IP FAIL: ' + e.message));
  });
}

function testDNS() {
  return new Promise((resolve) => {
    const req = https.get('https://aria-intel.fly.dev/health/live', { timeout: 8000 }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve('DNS OK: ' + d.slice(0, 80)));
    });
    req.on('error', e => resolve('DNS FAIL: ' + e.message));
  });
}

async function main() {
  console.log('Testing DNS...');
  console.log(await testDNS());
  console.log('Testing IP...');
  console.log(await testIP());
}

main().catch(e => console.log('ERROR: ' + e.message));
