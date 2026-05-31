# run_python.ps1 — Run Python with UTF-8 output encoding for PowerShell.
# Usage: .\_scripts\run_python.ps1 -c "print('hello')"
#        .\_scripts\run_python.ps1 script.py arg1 arg2

param(
    [string]$Inline,
    [string]$ScriptFile,
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ExtraArgs
)

$env:PYTHONIOENCODING = 'utf-8'

if ($Inline) {
    $result = &.venv\Scripts\python.exe -c $Inline 2>&1
} elseif ($ScriptFile) {
    $result = &.venv\Scripts\python.exe $ScriptFile @ExtraArgs 2>&1
} else {
    Write-Host "Usage: run_python.ps1 -c 'code' | run_python.ps1 -ScriptFile script.py [args]"
    exit 1
}

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
