// Test DNS vs IP connectivity from WA machine
const http = require('http');

// Test 1: DNS-based URL (should fail if DNS is broken)
console.log('Test 1: DNS-based URL (https://aria-intel.fly.dev)...');
const req1 = http.get('https://aria-intel.fly.dev/health/live', { timeout: 8000 }, (res) => {
  let d = '';
  res.on('data', c => d += c);
  res.on('end', () => console.log('  OK: ' + d.slice(0, 80)));
});
req1.on('error', e => console.log('  FAIL: ' + e.message));

// Test 2: IP-based URL (should work if network is fine)
console.log('Test 2: IP-based URL (http://[fdaa:60:7499:a7b:494:61da:69a4:2]:8000)...');
const req2 = http.get('http://[fdaa:60:7499:a7b:494:61da:69a4:2]:8000/health/live', { timeout: 8000 }, (res) => {
  let d = '';
  res.on('data', c => d += c);
  res.on('end', () => console.log('  OK: ' + d.slice(0, 80)));
});
req2.on('error', e => console.log('  FAIL: ' + e.message));
