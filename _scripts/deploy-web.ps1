# deploy-web.ps1 — Quick deploy to aria-web.
$env:PYTHONIOENCODING = 'utf-8'
$result = & powershell -NoProfile -ExecutionPolicy Unrestricted -Command "& .\scripts\deploy.ps1 -Web" 2>&1
$result | ForEach-Object { $_ -replace '[^\x20-\x7E\t\r\n]', '?' }
exit $LASTEXITCODE
