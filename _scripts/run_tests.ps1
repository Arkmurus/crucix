# run_tests.ps1 — Run pytest with UTF-8 output encoding.
# Usage: .\_scripts\run_tests.ps1 test_file.py [pytest args]

param(
    [Parameter(Position=0)]
    [string]$TestPath = "",
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$PytestArgs
)

$env:PYTHONIOENCODING = 'utf-8'

$cmd = @(".venv\Scripts\python.exe", "-m", "pytest")
if ($TestPath) {
    $cmd += $TestPath
}
$cmd += $PytestArgs

$result = & $cmd 2>&1

$result | ForEach-Object {
    if ($_ -is [string]) {
        $enc = [System.Text.Encoding]::ASCII
        $bytes = $enc.GetBytes($_)
        $enc.GetString($bytes)
    } else {
        $_
    }
}

exit $LASTEXITCODE
