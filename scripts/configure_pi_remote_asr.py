from __future__ import annotations

import argparse
from pathlib import Path


DEFAULTS = {
    "ASR_ENGINE": "remote_whisper",
    "REMOTE_ASR_TIMEOUT": "60",
    "VOSK_FALLBACK": "true",
}


def update_env(path: Path, values: dict[str, str]) -> None:
    existing = path.read_text(encoding="utf-8", errors="ignore").splitlines() if path.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in existing:
        if "=" not in line or line.lstrip().startswith("#"):
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in values:
            output.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in values.items():
        if key not in seen:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure Raspberry Pi guide project to use remote Whisper ASR.")
    parser.add_argument("--env", default=".env", help="Path to the Raspberry Pi project .env file")
    parser.add_argument("--remote-url", required=True, help="Remote ASR endpoint, e.g. http://100.x.x.x:9000/asr/transcribe")
    args = parser.parse_args()
    values = dict(DEFAULTS)
    values["REMOTE_ASR_URL"] = args.remote_url
    update_env(Path(args.env), values)
    print(f"Updated {args.env} for remote Whisper ASR: {args.remote_url}")


if __name__ == "__main__":
    main()
