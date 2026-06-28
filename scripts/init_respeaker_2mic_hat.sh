#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/yy-notabot/AI_RAG}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
INPUT_DEVICE="${VOICE_INPUT_DEVICE:-auto}"
OUTPUT_DEVICE="${VOICE_OUTPUT_DEVICE:-default}"
RECORD_SECONDS="${RECORD_SECONDS:-2}"
TEST_WAV="${TEST_WAV:-/tmp/airag_respeaker_test.wav}"

echo "[1/6] Audio devices"
arecord -l || true
aplay -l || true

echo "[2/6] SPI device check"
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_spi 0 || true
fi
sudo modprobe spi_bcm2835 || true
ls -l /dev/spidev* 2>/dev/null || echo "No /dev/spidev* yet; reboot may be required after enabling SPI."

echo "[3/6] Mixer baseline"
amixer sset Master 85% unmute 2>/dev/null || true
amixer sset Speaker 85% unmute 2>/dev/null || true
amixer sset PCM 85% unmute 2>/dev/null || true
amixer sset Capture 80% cap 2>/dev/null || true
amixer sset Mic 80% cap 2>/dev/null || true

echo "[4/6] LED ring test"
"$PYTHON_BIN" - <<'PY' || true
import time
from voice_service import Apa102LedRing

ring = Apa102LedRing(count=3, brightness=16)
for color in [(180, 20, 0), (0, 120, 20), (0, 60, 180)]:
    ring.set_all(color)
    time.sleep(0.6)
ring.off()
print("LED ring OK")
PY

echo "[5/6] Short microphone record"
if [[ "$INPUT_DEVICE" == "auto" ]]; then
  arecord -f S16_LE -r 16000 -c 1 -d "$RECORD_SECONDS" "$TEST_WAV"
else
  arecord -D "plughw:${INPUT_DEVICE},0" -f S16_LE -r 16000 -c 1 -d "$RECORD_SECONDS" "$TEST_WAV"
fi
ls -lh "$TEST_WAV"

echo "[6/6] Speaker playback test"
if [[ "$OUTPUT_DEVICE" == "default" || -z "$OUTPUT_DEVICE" ]]; then
  aplay "$TEST_WAV" || speaker-test -t sine -f 1000 -l 1 -s 1
else
  aplay -D "$OUTPUT_DEVICE" "$TEST_WAV" || speaker-test -D "$OUTPUT_DEVICE" -t sine -f 1000 -l 1 -s 1
fi

echo "ReSpeaker init/test complete. Env: $ENV_FILE"
