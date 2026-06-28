#!/usr/bin/env bash
set -euo pipefail

MMEDFD_REPO_DIR="${MMEDFD_REPO_DIR:-MMedFD}"
MMEDFD_DATA_DIR="${MMEDFD_DATA_DIR:-data/MMedFD}"
MODEL_SIZE="${MODEL_SIZE:-small}"
LANGUAGE="${LANGUAGE:-chinese}"
MODEL_NAME="${MODEL_NAME:-openai/whisper-small}"
OUTPUT_BASE_DIR="${OUTPUT_BASE_DIR:-output_models}"
TRAIN_DATASETS="${TRAIN_DATASETS:-${MMEDFD_DATA_DIR}/User/train/*.parquet}"
EVAL_DATASETS="${EVAL_DATASETS:-${MMEDFD_DATA_DIR}/User/eval/*.parquet}"

if [ ! -d "${MMEDFD_REPO_DIR}" ]; then
  git clone https://github.com/Kinetics-JOJO/MMedFD.git "${MMEDFD_REPO_DIR}"
fi

python -m pip install transformers datasets evaluate soundfile pandas pyarrow accelerate jiwer

cd "${MMEDFD_REPO_DIR}"

export MODEL_SIZE
export LANGUAGE
export MODEL_NAME
export TRAIN_DATASETS="../${TRAIN_DATASETS}"
export EVAL_DATASETS="../${EVAL_DATASETS}"
export OUTPUT_BASE_DIR="../${OUTPUT_BASE_DIR}"

if [ ! -f "run_train_asr.sh" ]; then
  echo "run_train_asr.sh was not found in ${MMEDFD_REPO_DIR}." >&2
  echo "Check the upstream MMedFD repository layout before training." >&2
  exit 1
fi

bash run_train_asr.sh
