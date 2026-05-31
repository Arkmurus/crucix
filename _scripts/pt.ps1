# pt.ps1 — pytest shortcut with ASCII-safe output.
$env:PYTHONIOENCODING = 'utf-8'
$python = "C:\code\crucix\.venv\Scripts\python.exe"
$allArgs = @("-m", "pytest") + $args
$result = & $python $allArgs 2>&1
$result | ForEach-Object { $_ -replace '[^\x20-\x7E\t\r\n]', '?' }
exit $LASTEXITCODE
