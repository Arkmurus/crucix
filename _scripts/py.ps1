# py.ps1 — Run Python with ASCII-safe output.
$env:PYTHONIOENCODING = 'utf-8'
$python = "C:\code\crucix\.venv\Scripts\python.exe"
$result = & $python $args 2>&1
$result | ForEach-Object { $_ -replace '[^\x20-\x7E\t\r\n]', '?' }
exit $LASTEXITCODE
