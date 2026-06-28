from __future__ import annotations

import json
import os
import queue
import struct
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

try:
    import pyaudio
except Exception:  # pragma: no cover - optional hardware dependency
    pyaudio = None

try:
    from vosk import KaldiRecognizer, Model
except Exception:  # pragma: no cover - optional ASR dependency
    KaldiRecognizer = None
    Model = None

try:
    from gpiozero import Button
    from gpiozero.pins.native import NativeFactory
except Exception:  # pragma: no cover - optional Raspberry Pi dependency
    Button = None
    NativeFactory = None

try:
    import RPi.GPIO as GPIO
except Exception:  # pragma: no cover - optional Raspberry Pi dependency
    GPIO = None

try:
    import spidev
except Exception:  # pragma: no cover - optional Raspberry Pi dependency
    spidev = None


load_dotenv()


@dataclass
class VoiceSettings:
    api_url: str = os.getenv("VOICE_CONSULT_URL", "http://127.0.0.1:8000/api/consult")
    status_path: Path = Path(os.getenv("VOICE_STATUS_PATH", "voice_status.json"))
    input_device: str = os.getenv("VOICE_INPUT_DEVICE", "auto")
    output_device: str = os.getenv("VOICE_OUTPUT_DEVICE", "default")
    asr_engine: str = os.getenv("ASR_ENGINE", "vosk")
    remote_asr_url: str = os.getenv("REMOTE_ASR_URL", "http://127.0.0.1:9000/asr/transcribe")
    remote_asr_timeout: float = float(os.getenv("REMOTE_ASR_TIMEOUT", "30"))
    vosk_fallback: bool = os.getenv("VOSK_FALLBACK", "true").lower() in {"1", "true", "yes", "on"}
    asr_model_path: str = os.getenv("ASR_MODEL_PATH", "models/vosk-model-small-cn-0.22")
    wake_word: str = os.getenv("WAKE_WORD", "小医小医")
    wake_words: List[str] = None
    trigger_mode: str = os.getenv("VOICE_TRIGGER_MODE", "button")
    button_gpio: int = int(os.getenv("VOICE_BUTTON_GPIO", "17"))
    button_hold_seconds: float = float(os.getenv("VOICE_BUTTON_HOLD_SECONDS", "0.15"))
    led_enabled: bool = os.getenv("VOICE_LED_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    led_count: int = int(os.getenv("VOICE_LED_COUNT", "3"))
    led_brightness: int = int(os.getenv("VOICE_LED_BRIGHTNESS", "24"))
    sample_rate: int = int(os.getenv("VOICE_SAMPLE_RATE", "16000"))
    chunk_size: int = int(os.getenv("VOICE_CHUNK_SIZE", "4000"))
    record_seconds: float = float(os.getenv("VOICE_RECORD_SECONDS", "6"))
    tts_engine: str = os.getenv("TTS_ENGINE", "espeak-ng")
    tts_voice_path: str = os.getenv("TTS_VOICE_PATH", "")

    def __post_init__(self) -> None:
        words = os.getenv("WAKE_WORDS", self.wake_word)
        self.wake_words = [word.strip().replace(" ", "") for word in words.split(",") if word.strip()]


class VoiceStatus:
    def __init__(self, settings: VoiceSettings):
        self.settings = settings

    def write(self, **values: Any) -> None:
        payload: Dict[str, Any] = {
            "state": values.pop("state", "unknown"),
            "message": values.pop("message", ""),
            "input_device": self.settings.input_device,
            "output_device": self.settings.output_device,
            "asr_engine": self.settings.asr_engine,
            "remote_asr_url": self.settings.remote_asr_url,
            "asr_model_path": self.settings.asr_model_path,
            "wake_word": self.settings.wake_word,
            "wake_words": self.settings.wake_words,
            "trigger_mode": self.settings.trigger_mode,
            "button_gpio": self.settings.button_gpio,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        payload.update(values)
        self.settings.status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class VoiceAssistant:
    def __init__(self, settings: VoiceSettings):
        self.settings = settings
        self.status = VoiceStatus(settings)
        self.audio = None
        self.model = None
        self.button = None
        self.leds = NullLedRing()

    def run_forever(self) -> None:
        self.status.write(state="starting", message="语音服务启动中。")
        while True:
            try:
                self._ensure_ready()
                if self.settings.trigger_mode == "button":
                    self.status.write(state="listening", message="等待按住 ReSpeaker 按键录音。")
                    self._wait_for_button_press()
                    self.status.write(state="recording", message="按键已按下，正在录音；松开按键结束。")
                    self.leds.set_all((0, 60, 180))
                    audio_path = self._record_while_button_pressed()
                else:
                    self.status.write(state="listening", message="等待热词唤醒。")
                    self._listen_for_wake_word()
                    self.status.write(state="recording", message="已唤醒，正在录制问诊语音。")
                    self.leds.set_all((0, 60, 180))
                    audio_path = self._record_question()
                self.leds.off()
                transcript = self._recognize_file(audio_path)
                if not transcript:
                    self.status.write(state="listening", message="未识别到有效语音，继续等待热词。")
                    continue
                self.status.write(state="consulting", message="已识别语音，正在查询问诊系统。", last_transcript=transcript)
                result = self._consult(transcript)
                speech = self._build_speech(result)
                self.status.write(
                    state="speaking",
                    message="正在播报问诊结果。",
                    last_transcript=transcript,
                    last_advice=speech,
                )
                self.leds.set_all((0, 120, 20))
                self._speak(speech)
                self.leds.off()
                self.status.write(
                    state="listening",
                    message="播报完成，继续等待热词。",
                    last_transcript=transcript,
                    last_advice=speech,
                )
            except Exception as exc:
                self.leds.set_all((180, 20, 0))
                self.status.write(state="degraded", message="语音服务降级，稍后重试。", last_error=str(exc))
                time.sleep(5)
                self.leds.off()

    def _ensure_ready(self) -> None:
        if pyaudio is None:
            raise RuntimeError("PyAudio 未安装，请先安装 portaudio19-dev 并执行 pip install -r requirements-voice.txt。")
        needs_vosk = (
            self.settings.asr_engine == "vosk"
            or self.settings.vosk_fallback
            or self.settings.trigger_mode != "button"
        )
        if needs_vosk:
            if Model is None or KaldiRecognizer is None:
                raise RuntimeError("Vosk 未安装，请执行 pip install -r requirements-voice.txt。")
            model_path = Path(self.settings.asr_model_path)
            if not model_path.exists():
                raise RuntimeError(f"ASR 模型不存在：{model_path}")
        if self.audio is None:
            self.audio = pyaudio.PyAudio()
            self.settings.input_device = self._resolve_input_device()
        if needs_vosk and self.model is None:
            self.model = Model(str(model_path))
        if self.settings.trigger_mode == "button" and self.button is None:
            self.button = create_button(self.settings.button_gpio)
        if self.settings.led_enabled and isinstance(self.leds, NullLedRing):
            self.leds = Apa102LedRing(self.settings.led_count, self.settings.led_brightness)
            self.leds.off()

    def _resolve_input_device(self) -> str:
        if self.settings.input_device != "auto":
            return self.settings.input_device
        for index in range(self.audio.get_device_count()):
            info = self.audio.get_device_info_by_index(index)
            name = str(info.get("name", ""))
            if info.get("maxInputChannels", 0) > 0 and ("seeed" in name.lower() or "respeaker" in name.lower()):
                return str(index)
        default = self.audio.get_default_input_device_info()
        return str(default["index"])

    def _open_input_stream(self):
        device_index = int(self.settings.input_device) if self.settings.input_device.isdigit() else None
        return self.audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.settings.sample_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=self.settings.chunk_size,
        )

    def _listen_for_wake_word(self) -> None:
        recognizer = KaldiRecognizer(self.model, self.settings.sample_rate)
        stream = self._open_input_stream()
        stream.start_stream()
        try:
            while True:
                data = stream.read(self.settings.chunk_size, exception_on_overflow=False)
                if recognizer.AcceptWaveform(data):
                    text = json.loads(recognizer.Result()).get("text", "").replace(" ", "")
                    if any(wake_word in text for wake_word in self.settings.wake_words):
                        return
        finally:
            stream.stop_stream()
            stream.close()

    def _wait_for_button_press(self) -> None:
        while not self.button.is_pressed:
            time.sleep(0.05)
        time.sleep(self.settings.button_hold_seconds)

    def _record_while_button_pressed(self) -> Path:
        stream = self._open_input_stream()
        stream.start_stream()
        frames: List[bytes] = []
        try:
            while self.button.is_pressed:
                frames.append(stream.read(self.settings.chunk_size, exception_on_overflow=False))
        finally:
            stream.stop_stream()
            stream.close()
        if not frames:
            raise RuntimeError("按键录音时间太短，未采集到音频。")

        handle = tempfile.NamedTemporaryFile(prefix="airag_button_question_", suffix=".wav", delete=False)
        path = Path(handle.name)
        handle.close()
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(self.audio.get_sample_size(pyaudio.paInt16))
            wav.setframerate(self.settings.sample_rate)
            wav.writeframes(b"".join(frames))
        return path

    def _record_question(self) -> Path:
        stream = self._open_input_stream()
        stream.start_stream()
        frames: List[bytes] = []
        frame_count = int(self.settings.sample_rate / self.settings.chunk_size * self.settings.record_seconds)
        try:
            for _ in range(frame_count):
                frames.append(stream.read(self.settings.chunk_size, exception_on_overflow=False))
        finally:
            stream.stop_stream()
            stream.close()

        handle = tempfile.NamedTemporaryFile(prefix="airag_question_", suffix=".wav", delete=False)
        path = Path(handle.name)
        handle.close()
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(self.audio.get_sample_size(pyaudio.paInt16))
            wav.setframerate(self.settings.sample_rate)
            wav.writeframes(b"".join(frames))
        return path

    def _recognize_file(self, audio_path: Path) -> str:
        if self.settings.asr_engine == "remote_whisper":
            try:
                return self._recognize_remote_whisper(audio_path)
            except Exception as exc:
                if not self.settings.vosk_fallback:
                    raise
                self.status.write(
                    state="recognizing",
                    message="远程 Whisper ASR 失败，正在回退本地 Vosk。",
                    last_asr_engine="vosk",
                    last_asr_error=str(exc),
                )
        return self._recognize_vosk(audio_path)

    def _recognize_remote_whisper(self, audio_path: Path) -> str:
        start = time.perf_counter()
        with httpx.Client(timeout=self.settings.remote_asr_timeout, trust_env=False) as client:
            with audio_path.open("rb") as audio_file:
                files = {"file": (audio_path.name, audio_file, "audio/wav")}
                response = client.post(self.settings.remote_asr_url, files=files)
        response.raise_for_status()
        data = response.json()
        transcript = str(data.get("text", "")).replace(" ", "").strip()
        latency_ms = int((time.perf_counter() - start) * 1000)
        self.status.write(
            state="recognizing",
            message="远程 Whisper ASR 识别完成。",
            last_asr_engine="remote_whisper",
            last_asr_latency_ms=latency_ms,
            last_transcript=transcript,
        )
        return transcript

    def _recognize_vosk(self, audio_path: Path) -> str:
        recognizer = KaldiRecognizer(self.model, self.settings.sample_rate)
        with wave.open(str(audio_path), "rb") as wav:
            while True:
                data = wav.readframes(4000)
                if not data:
                    break
                recognizer.AcceptWaveform(data)
        result = json.loads(recognizer.FinalResult())
        transcript = str(result.get("text", "")).replace(" ", "").strip()
        self.status.write(
            state="recognizing",
            message="本地 Vosk ASR 识别完成。",
            last_asr_engine="vosk",
            last_transcript=transcript,
        )
        return transcript

    def _consult(self, transcript: str) -> Dict[str, Any]:
        with httpx.Client(timeout=60, trust_env=False) as client:
            response = client.post(self.settings.api_url, json={"query": transcript})
            response.raise_for_status()
            return response.json()

    def _build_speech(self, result: Dict[str, Any]) -> str:
        diseases = "、".join(result.get("diseases", [])[:3]) or "暂未匹配到明确病名"
        departments = "、".join(result.get("departments", [])[:3]) or "全科"
        advice = str(result.get("advice", "建议咨询医生。"))
        return f"可能方向：{diseases}。推荐科室：{departments}。{advice} 请注意，这不是确诊或处方。"

    def _speak(self, text: str) -> None:
        if self.settings.tts_engine == "piper" and self.settings.tts_voice_path:
            self._speak_with_piper(text)
            return
        self._speak_with_espeak(text)

    def _speak_with_piper(self, text: str) -> None:
        with tempfile.NamedTemporaryFile(prefix="airag_tts_", suffix=".wav", delete=False) as wav:
            wav_path = Path(wav.name)
        subprocess.run(
            ["piper", "--model", self.settings.tts_voice_path, "--output_file", str(wav_path)],
            input=text,
            text=True,
            check=True,
        )
        self._play_wav(wav_path)

    def _speak_with_espeak(self, text: str) -> None:
        with tempfile.NamedTemporaryFile(prefix="airag_espeak_", suffix=".wav", delete=False) as wav:
            wav_path = Path(wav.name)
        subprocess.run(["espeak-ng", "-v", "zh", "-s", "150", "-w", str(wav_path), text], check=True)
        self._play_wav(wav_path)

    def _play_wav(self, wav_path: Path) -> None:
        cmd = ["aplay"]
        if self.settings.output_device and self.settings.output_device != "default":
            cmd += ["-D", self.settings.output_device]
        cmd.append(str(wav_path))
        subprocess.run(cmd, check=True)


def main() -> None:
    settings = VoiceSettings()
    assistant = VoiceAssistant(settings)
    assistant.run_forever()


class NullLedRing:
    def set_all(self, rgb: tuple) -> None:
        return

    def off(self) -> None:
        return


class Apa102LedRing:
    def __init__(self, count: int, brightness: int):
        if spidev is None:
            raise RuntimeError("spidev 未安装，无法控制 ReSpeaker APA102 LED。")
        self.count = count
        self.brightness = max(0, min(31, brightness))
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = 8000000

    def set_all(self, rgb: tuple) -> None:
        red, green, blue = rgb
        frame = [0x00, 0x00, 0x00, 0x00]
        for _ in range(self.count):
            frame.extend([0xE0 | self.brightness, blue & 0xFF, green & 0xFF, red & 0xFF])
        frame.extend([0xFF, 0xFF, 0xFF, 0xFF])
        self.spi.xfer2(frame)

    def off(self) -> None:
        self.set_all((0, 0, 0))


def create_button(gpio_pin: int):
    if Button is not None:
        pin_factory = NativeFactory() if NativeFactory is not None else None
        return Button(gpio_pin, pull_up=True, bounce_time=0.05, pin_factory=pin_factory)
    if GPIO is not None:
        return RpiGpioButton(gpio_pin)
    raise RuntimeError("未安装 GPIO 按键依赖，请安装 RPi.GPIO 或 gpiozero。")


class RpiGpioButton:
    def __init__(self, gpio_pin: int):
        self.gpio_pin = gpio_pin
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(gpio_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    @property
    def is_pressed(self) -> bool:
        return GPIO.input(self.gpio_pin) == GPIO.LOW


if __name__ == "__main__":
    main()
