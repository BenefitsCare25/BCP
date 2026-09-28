$ErrorActionPreference = 'Stop'
$reviewPort = 4178
try {
    $reviewHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$reviewPort/health" -TimeoutSec 2
    if ($reviewHealth.service -eq 'inspro-ai-governance-design-review') {
        Write-Output "Already running: http://127.0.0.1:$reviewPort"
        exit 0
    }
} catch {
    # The server is not running yet.
}
$reviewPython = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\backend\.venv\Scripts\python.exe'))
if (-not (Test-Path -LiteralPath $reviewPython)) {
    $reviewPython = (Get-Command python -ErrorAction Stop).Source
}
$reviewProcess = Start-Process -FilePath $reviewPython -ArgumentList @('-u', 'serve_review.py', '--port', "$reviewPort") -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'server-stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'server-stderr.log') -PassThru
for ($reviewAttempt = 0; $reviewAttempt -lt 20; $reviewAttempt++) {
    Start-Sleep -Milliseconds 250
    $reviewProcess.Refresh()
    if ($reviewProcess.HasExited) { throw 'Preview server exited. See server-stderr.log.' }
    try {
        $reviewHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$reviewPort/health" -TimeoutSec 1
        if ($reviewHealth.service -eq 'inspro-ai-governance-design-review') {
            Write-Output "Running: http://127.0.0.1:$reviewPort (PID $($reviewProcess.Id))"
            exit 0
        }
    } catch {
        # Give the background process time to bind.
    }
}
throw 'Could not verify the preview server. See server-stderr.log.'
