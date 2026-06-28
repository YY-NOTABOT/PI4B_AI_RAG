param(
    [string]$ProjectRoot = "D:\amzprojects\AI_RAG",
    [string]$PythonPath = "D:\app\python interpreter 3.12\python.exe",
    [string]$PiUser = "yy-notabot",
    [string]$PiHost = "100.88.185.67",
    [string]$LocalTailscaleIP = "100.86.22.56",
    [int]$WebPort = 8000,
    [int]$AsrPort = 9000,
    [switch]$SkipPi,
    [switch]$KeepExistingLocal
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Stop-PortListener {
    param([int]$Port)
    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    $processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($processId in $processIds) {
        if (-not $processId) {
            continue
        }
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($process) {
            Write-Host "Stopping process $($process.Id) on port $Port ($($process.ProcessName))"
            Stop-Process -Id $process.Id -Force
        }
    }
}

function Wait-HttpJson {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 120
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = $null
    while ((Get-Date) -lt $deadline) {
        try {
            return Invoke-RestMethod -Uri $Url -TimeoutSec 10
        }
        catch {
            $lastError = $_.Exception.Message
            Start-Sleep -Seconds 2
        }
    }
    throw "Timed out waiting for $Url. Last error: $lastError"
}

function Start-LocalWeb {
    $outLog = Join-Path $ProjectRoot "logs\local_web_server.out.log"
    $errLog = Join-Path $ProjectRoot "logs\local_web_server.err.log"
    Start-Process `
        -FilePath $PythonPath `
        -ArgumentList @("-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "$WebPort") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -PassThru
}

function Start-LocalAsr {
    $env:WHISPER_MODEL_PATH = "models\whisper-large-v3-medical-best-checkpoint-200"
    $env:WHISPER_MODEL_NAME = "whisper-large-v3-medical"
    $env:WHISPER_LANGUAGE = "chinese"
    $env:WHISPER_TASK = "transcribe"
    $env:WHISPER_DEVICE = "auto"
    $env:WHISPER_TORCH_DTYPE = "auto"
    $env:WHISPER_CHUNK_LENGTH_S = "30"
    $env:WHISPER_BATCH_SIZE = "4"

    $outLog = Join-Path $ProjectRoot "logs\local_asr_server.out.log"
    $errLog = Join-Path $ProjectRoot "logs\local_asr_server.err.log"
    Start-Process `
        -FilePath $PythonPath `
        -ArgumentList @("-m", "uvicorn", "asr_server.app:app", "--host", "0.0.0.0", "--port", "$AsrPort") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -PassThru
}

if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    throw "ProjectRoot not found: $ProjectRoot"
}
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "PythonPath not found: $PythonPath"
}

New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "logs") | Out-Null

Write-Step "Starting local Windows services"
if (-not $KeepExistingLocal) {
    Stop-PortListener -Port $WebPort
    Stop-PortListener -Port $AsrPort
    Start-Sleep -Seconds 2
}

$webProcess = Start-LocalWeb
Write-Host "Local web process started: PID $($webProcess.Id), URL http://127.0.0.1:$WebPort"

$asrProcess = Start-LocalAsr
Write-Host "Local ASR process started: PID $($asrProcess.Id), URL http://127.0.0.1:$AsrPort/asr/health"

Write-Step "Checking local services"
$localHealth = Wait-HttpJson -Url "http://127.0.0.1:$WebPort/api/health/status" -TimeoutSeconds 90
Write-Host "Local health provider: $($localHealth.provider), available=$($localHealth.available)"

$asrHealth = Wait-HttpJson -Url "http://127.0.0.1:$AsrPort/asr/health" -TimeoutSeconds 180
Write-Host "Local ASR model: $($asrHealth.model), device=$($asrHealth.device), dtype=$($asrHealth.dtype)"

if (-not $SkipPi) {
    Write-Step "Restarting Raspberry Pi services"
    $sshTarget = "$PiUser@$PiHost"
    $remoteCommand = @"
cd /home/$PiUser/AI_RAG &&
sudo systemctl restart neo4j airag-web airag-voice &&
sleep 5 &&
systemctl is-active neo4j airag-web airag-voice &&
curl -sS --max-time 20 http://$LocalTailscaleIP`:$AsrPort/asr/health &&
echo &&
curl -sS --max-time 20 http://127.0.0.1:$WebPort/api/voice/status
"@
    ssh -o BatchMode=yes -o ConnectTimeout=15 $sshTarget $remoteCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Raspberry Pi startup/check failed. If SSH key login is unavailable, run ssh $sshTarget once manually and enter the password."
    }
}

Write-Step "Ready"
Write-Host "PC web / health: http://127.0.0.1:$WebPort"
Write-Host "PC Whisper ASR:  http://127.0.0.1:$AsrPort/asr/health"
Write-Host "Pi web:          http://$PiHost`:$WebPort"
Write-Host "Pi voice status: http://$PiHost`:$WebPort/api/voice/status"
Write-Host ""
Write-Host "Logs:"
Write-Host "  $ProjectRoot\logs\local_web_server.out.log"
Write-Host "  $ProjectRoot\logs\local_web_server.err.log"
Write-Host "  $ProjectRoot\logs\local_asr_server.out.log"
Write-Host "  $ProjectRoot\logs\local_asr_server.err.log"
