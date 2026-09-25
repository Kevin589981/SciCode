#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${SCICODE_REPO_ROOT:-/root/ScienceIDE-workspace/SciCode-v3}"
DATA_ROOT="${SCICODE_DATA_ROOT:-/root/ScienceIDE-workspace/SciCode}"
OUTPUT_ROOT="${SCICODE_OUTPUT_ROOT:-${REPO_ROOT}/data-reasoning-v3-smoke}"
PYTHON_BIN="${SCICODE_FACTORY_PYTHON:-/root/scicode-factory-venv/bin/python}"
MODEL="${SCICODE_LLM_MODEL:-Kimi-K3}"

export SCICODE_LLM_BASE_URL="${SCICODE_LLM_BASE_URL:-http://10.100.184.127:5050/v1}"
export SCICODE_LLM_API_KEY="${SCICODE_LLM_API_KEY:-dummy}"
export SCICODE_LLM_MODEL="${MODEL}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost,10.100.184.127}"

if [[ "${1:-}" != "--execute" ]]; then
  echo "Dry run. Run bash factory/reasoning/run_v3_smoke.sh --execute to create ${OUTPUT_ROOT}."
  exit 0
fi

cd "${REPO_ROOT}"
exec "${PYTHON_BIN}" -m factory.reasoning.batch auto \
  --output-root "${OUTPUT_ROOT}" \
  --cache-root "${DATA_ROOT}/.cache/reasoning-repositories" \
  --db "${OUTPUT_ROOT}/batch.sqlite3" \
  --repository-source scicodepile \
  --scicodepile-catalog "${SCICODEPILE_CATALOG:-${DATA_ROOT}/.cache/scicodepile/catalog.jsonl}" \
  --repository-limit 3 \
  --target-sft-rows 2 \
  --workers 3 \
  --repository-slots 3 \
  --llm-slots 3 \
  --job-lease-seconds 14400 \
  --tasks-per-repo 3 \
  --min-tasks-per-repo 3 \
  --reservation-rows-per-repo 1 \
  --max-mined-candidates 100 \
  --profile-model "${MODEL}" \
  --author-model "${MODEL}" \
  --critic-model "${MODEL}" \
  --verifier-model "${MODEL}" \
  --solver-model "${MODEL}" \
  --judge-model "${MODEL}" \
  --context-window-tokens 262144 \
  --author-max-tokens 32768 \
  --solver-max-tokens 16384 \
  --critic-max-tokens 4096 \
  --verifier-max-tokens 16384 \
  --judge-max-tokens 16384 \
  --judge-max-input-chars 100000 \
  --timeout 2400 \
  --pipeline-concurrency 1
