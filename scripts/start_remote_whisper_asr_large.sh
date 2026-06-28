#!/usr/bin/env bash
set -euo pipefail

cd "${AI_RAG_DIR:-/root/autodl-tmp/AI_RAG}"
export PATH=/root/miniconda3/bin:$PATH

export WHISPER_MODEL_PATH="${WHISPER_MODEL_PATH:-output_models/whisper-large-v3-medical/checkpoint-200}"
export WHISPER_MODEL_NAME="${WHISPER_MODEL_NAME:-whisper-large-v3-medical-checkpoint-200}"
export WHISPER_LANGUAGE="${WHISPER_LANGUAGE:-chinese}"
export WHISPER_TASK="${WHISPER_TASK:-transcribe}"
export WHISPER_DEVICE="${WHISPER_DEVICE:-auto}"
export WHISPER_TORCH_DTYPE="${WHISPER_TORCH_DTYPE:-bf16}"
export WHISPER_CHUNK_LENGTH_S="${WHISPER_CHUNK_LENGTH_S:-30}"
export WHISPER_BATCH_SIZE="${WHISPER_BATCH_SIZE:-4}"

python -m uvicorn asr_server.app:app --host 0.0.0.0 --port "${ASR_PORT:-9000}"
