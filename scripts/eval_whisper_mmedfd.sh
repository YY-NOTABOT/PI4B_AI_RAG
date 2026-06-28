#!/usr/bin/env bash
set -euo pipefail

MMEDFD_REPO_DIR="${MMEDFD_REPO_DIR:-MMedFD}"
BASE_MODEL_NAME="${BASE_MODEL_NAME:-openai/whisper-small}"
CHECKPOINT_DIR="${CHECKPOINT_DIR:-output_models/whisper}"
TEST_DATASET_PATH="${TEST_DATASET_PATH:-data/MMedFD/User/eval/user_eval.parquet}"
OUTPUT_DIR="${OUTPUT_DIR:-whisper_inference_results}"
CHUNK_LENGTH_S="${CHUNK_LENGTH_S:-30}"
BATCH_SIZE="${BATCH_SIZE:-8}"
LANGUAGE="${LANGUAGE:-chinese}"
MERGE_ON="${MERGE_ON:-ID}"

if [ ! -d "${MMEDFD_REPO_DIR}" ]; then
  echo "${MMEDFD_REPO_DIR} does not exist. Run scripts/train_whisper_mmedfd.sh first." >&2
  exit 1
fi

cd "${MMEDFD_REPO_DIR}"

python whisper_asr_infer.py \
  --base_model_name "${BASE_MODEL_NAME}" \
  --checkpoint_dir "../${CHECKPOINT_DIR}" \
  --test_dataset_path "../${TEST_DATASET_PATH}" \
  --output_dir "../${OUTPUT_DIR}" \
  --chunk_length_s "${CHUNK_LENGTH_S}" \
  --batch_size "${BATCH_SIZE}" \
  --language "${LANGUAGE}"

python compute_score.py \
  --predict_path "../${OUTPUT_DIR}/predictions.csv" \
  --groundtruth_path "../${TEST_DATASET_PATH}" \
  --merge_on "${MERGE_ON}"

