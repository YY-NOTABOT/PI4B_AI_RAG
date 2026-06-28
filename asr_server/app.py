from __future__ import annotations

import os
import tempfile
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI, File, HTTPException, UploadFile
from scipy.signal import resample_poly
from pydantic import BaseModel
from transformers import WhisperForConditionalGeneration, WhisperProcessor


class AsrResponse(BaseModel):
    text: str
    language: str
    model: str
    duration: float
    latency_ms: int


class AsrHealthResponse(BaseModel):
    status: str
    model: str
    device: str
    dtype: str


MODEL_PATH = os.getenv("WHISPER_MODEL_PATH", "output_models/whisper")
MODEL_NAME = os.getenv("WHISPER_MODEL_NAME", "whisper-small-mmedfd")
LANGUAGE = os.getenv("WHISPER_LANGUAGE", "chinese")
TASK = os.getenv("WHISPER_TASK", "transcribe")
DEVICE = "cuda:0" if torch.cuda.is_available() and os.getenv("WHISPER_DEVICE", "auto") != "cpu" else "cpu"
DTYPE_NAME = os.getenv("WHISPER_TORCH_DTYPE", "auto").lower()
if DTYPE_NAME in {"bf16", "bfloat16"}:
    DTYPE = torch.bfloat16
elif DTYPE_NAME in {"fp16", "float16"}:
    DTYPE = torch.float16
elif DTYPE_NAME in {"fp32", "float32"}:
    DTYPE = torch.float32
else:
    DTYPE = torch.float16 if DEVICE.startswith("cuda") else torch.float32
CHUNK_LENGTH_S = int(os.getenv("WHISPER_CHUNK_LENGTH_S", "30"))
BATCH_SIZE = int(os.getenv("WHISPER_BATCH_SIZE", "4"))


app = FastAPI(title="Remote Whisper Medical ASR", version="0.1.0")
processor: Optional[WhisperProcessor] = None
asr_model: Optional[WhisperForConditionalGeneration] = None


@app.on_event("startup")
def load_model() -> None:
    global processor, asr_model
    processor = WhisperProcessor.from_pretrained(
        MODEL_PATH,
        language=LANGUAGE,
        task=TASK,
    )
    asr_model = WhisperForConditionalGeneration.from_pretrained(
        MODEL_PATH,
        torch_dtype=DTYPE,
        low_cpu_mem_usage=True,
        use_safetensors=True,
    ).to(torch.device(DEVICE))
    asr_model.eval()


@app.get("/asr/health", response_model=AsrHealthResponse)
def health() -> AsrHealthResponse:
    return AsrHealthResponse(
        status="ok" if asr_model is not None and processor is not None else "loading",
        model=MODEL_NAME,
        device=DEVICE,
        dtype=str(DTYPE).replace("torch.", ""),
    )


@app.post("/asr/transcribe", response_model=AsrResponse)
async def transcribe(file: UploadFile = File(...)) -> AsrResponse:
    if asr_model is None or processor is None:
        raise HTTPException(status_code=503, detail="ASR model is not loaded")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing audio filename")

    suffix = Path(file.filename).suffix or ".wav"
    with tempfile.NamedTemporaryFile(prefix="remote_asr_", suffix=suffix, delete=False) as handle:
        audio_path = Path(handle.name)
        handle.write(await file.read())

    start = time.perf_counter()
    try:
        duration = wav_duration(audio_path)
        audio_input = load_audio(audio_path)
        input_features = processor.feature_extractor(
            audio_input,
            sampling_rate=16000,
            return_tensors="pt",
        ).input_features.to(torch.device(DEVICE))
        if DEVICE.startswith("cuda") and DTYPE in {torch.float16, torch.bfloat16}:
            input_features = input_features.to(DTYPE)
        with torch.no_grad():
            generated_ids = asr_model.generate(
                input_features,
                language=LANGUAGE,
                task=TASK,
                max_new_tokens=180,
                num_beams=1,
            )
        text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()
    finally:
        try:
            audio_path.unlink()
        except OSError:
            pass

    return AsrResponse(
        text=text,
        language="zh",
        model=MODEL_NAME,
        duration=duration,
        latency_ms=int((time.perf_counter() - start) * 1000),
    )


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            return round(wav.getnframes() / float(wav.getframerate()), 3)
    except wave.Error:
        return 0.0


def load_audio(path: Path) -> np.ndarray:
    audio, sample_rate = sf.read(str(path), always_2d=False)
    array = np.asarray(audio)
    if array.ndim > 1:
        array = array.mean(axis=1)
    array = array.astype("float32")
    if int(sample_rate) != 16000:
        array = resample_poly(array, 16000, int(sample_rate)).astype("float32")
    return array
