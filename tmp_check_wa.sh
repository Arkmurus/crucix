#!/bin/sh
# Check WA health
echo "=== PROCESSES ==="
ps aux | grep -i node | head -10

echo ""
echo "=== PORT 5070 ==="
ss -tlnp | grep 5070 || echo "NOT LISTENING"

echo ""
echo "=== FILES ==="
ls -la /app/ 2>/dev/null | head -20

echo ""
echo "=== PACKAGE.JSON ==="
cat /app/package.json 2>/dev/null | head -30

echo ""
echo "=== SERVER FILES ==="
ls -la /app/*.mjs /app/*.js 2>/dev/null | head -20

echo ""
echo "=== LOGS ==="
ls -la /app/logs/ 2>/dev/null || echo "No logs dir"
cat /app/logs/*.log 2>/dev/null | tail -50 || echo "No log files"
