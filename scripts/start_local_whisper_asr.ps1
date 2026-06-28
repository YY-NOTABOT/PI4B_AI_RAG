param(
    [string]$ModelPath = "models\whisper-large-v3-medical-best-checkpoint-200",
    [int]$Port = 9000,
    [string]$Device = "auto",
    [string]$DType = "auto"
)

$ErrorActionPreference = "Stop"
$env:WHISPER_MODEL_PATH = $ModelPath
$env:WHISPER_MODEL_NAME = "whisper-large-v3-medical"
$env:WHISPER_LANGUAGE = "chinese"
$env:WHISPER_TASK = "transcribe"
$env:WHISPER_DEVICE = $Device
$env:WHISPER_TORCH_DTYPE = $DType
$env:WHISPER_CHUNK_LENGTH_S = "30"
$env:WHISPER_BATCH_SIZE = "4"

python -m uvicorn asr_server.app:app --host 0.0.0.0 --port $Port
