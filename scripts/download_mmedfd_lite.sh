#!/usr/bin/env bash
set -euo pipefail

DATASET_REPO="${DATASET_REPO:-HanselZz/MMedFD}"
DATASET_DIR="${DATASET_DIR:-data/MMedFD}"
HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

python -m pip install -U huggingface_hub hf_transfer datasets pyarrow soundfile

export HF_HUB_ENABLE_HF_TRANSFER="${HF_HUB_ENABLE_HF_TRANSFER:-1}"
export HF_XET_HIGH_PERFORMANCE="${HF_XET_HIGH_PERFORMANCE:-1}"
export HF_ENDPOINT

hf download "${DATASET_REPO}" \
  --repo-type dataset \
  --local-dir "${DATASET_DIR}"

python - <<'PY'
from pathlib import Path
import pyarrow.parquet as pq

root = Path("data/MMedFD")
files = sorted(root.rglob("*.parquet"))
print(f"parquet_files={len(files)}")
if not files:
    raise SystemExit("No parquet files were downloaded.")
sample = pq.read_schema(files[0])
print(f"sample_file={files[0]}")
print(f"schema={sample}")
PY
