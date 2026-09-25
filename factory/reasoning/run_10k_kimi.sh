#!/usr/bin/env bash
set -euo pipefail

# Prepared production launcher. It is intentionally inert without a mode.
MODE="${1:-}"
if [[ "${MODE}" != "--execute" && "${MODE}" != "--prepare-and-enqueue" && "${MODE}" != "--enqueue-expanded" ]]; then
  cat <<'EOF'
Dry configuration only; no batch was started.
target_sft_rows=10000
workers=500
dynamic_llm_concurrency=500..1792
context_window_tokens=262144
author_max_tokens=131072
solver_max_tokens=196608
verifier_max_tokens=65536
tasks_per_repo=3..16
reservation_rows_per_repo=5
initial_repository_limit=3600
expanded_repository_limit=19559
repository_source=SciCodePile_clean_dataset_only
Run initial catalog: bash factory/reasoning/run_10k_kimi.sh --execute
Expand the cleaned dataset and enqueue: bash factory/reasoning/run_10k_kimi.sh --prepare-and-enqueue
EOF
  exit 0
fi

REPO_ROOT="${SCICODE_REPO_ROOT:-/root/ScienceIDE-workspace/SciCode-v3}"
DATA_ROOT="${SCICODE_DATA_ROOT:-/root/ScienceIDE-workspace/SciCode}"
OUTPUT_ROOT="${SCICODE_OUTPUT_ROOT:-${REPO_ROOT}/data-reasoning-10k-v3}"
CACHE_ROOT="${SCICODE_CACHE_ROOT:-${DATA_ROOT}/.cache/reasoning-repositories}"
PYTHON_BIN="${SCICODE_FACTORY_PYTHON:-/root/scicode-factory-venv/bin/python}"
METRICS_URL="${SCICODE_LLM_METRICS_URL:-http://10.100.184.127:29000/metrics}"
MODEL="${SCICODE_LLM_MODEL:-Kimi-K3}"
SCICODEPILE_CATALOG="${SCICODEPILE_CATALOG:-${DATA_ROOT}/.cache/scicodepile/catalog.jsonl}"
EXPANDED_CATALOG="${SCICODEPILE_EXPANDED_CATALOG:-${DATA_ROOT}/.cache/scicodepile/catalog-10k.jsonl}"

export SCICODE_LLM_BASE_URL="${SCICODE_LLM_BASE_URL:-http://10.100.184.127:5050/v1}"
export SCICODE_LLM_API_KEY="${SCICODE_LLM_API_KEY:-dummy}"
export SCICODE_LLM_MODEL="${MODEL}"
export HTTP_PROXY="${HTTP_PROXY:-http://httpproxy-headless.kubebrain.svc.lg.shzhisuan.local:3128}"
export HTTPS_PROXY="${HTTPS_PROXY:-${HTTP_PROXY}}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost,10.100.184.127}"

cd "${REPO_ROOT}"

RECIPE_ARGS=(
  --reservation-rows-per-repo 5
  --tasks-per-repo 16
  --min-tasks-per-repo 3
  --max-mined-candidates 1200
  --profile-model "${MODEL}"
  --author-model "${MODEL}"
  --critic-model "${MODEL}"
  --verifier-model "${MODEL}"
  --solver-model "${MODEL}"
  --judge-model "${MODEL}"
  --context-window-tokens 262144
  --author-max-tokens 131072
  --solver-max-tokens 196608
  --critic-max-tokens 16384
  --verifier-max-tokens 65536
  --judge-max-tokens 32768
  --judge-max-input-chars 900000
  --timeout 7200
  --pipeline-concurrency 4
)

if [[ "${MODE}" == "--prepare-and-enqueue" ]]; then
  "${PYTHON_BIN}" -m factory.reasoning.scicodepile_dataset \
    --raw-dir "${DATA_ROOT}/.cache/scicodepile/raw" \
    --prepared-root "${DATA_ROOT}/.cache/scicodepile/prepared" \
    --catalog "${EXPANDED_CATALOG}" \
    --repository-limit 19559 \
    --skip-download
  exec bash "${BASH_SOURCE[0]}" --enqueue-expanded
fi

if [[ "${MODE}" == "--enqueue-expanded" ]]; then
  if [[ ! -s "${EXPANDED_CATALOG}" || ! -s "${OUTPUT_ROOT}/batch.sqlite3" ]]; then
    echo "Expanded catalog or active 10k queue is missing" >&2
    exit 2
  fi
  exec "${PYTHON_BIN}" -m factory.reasoning.batch enqueue \
    --db "${OUTPUT_ROOT}/batch.sqlite3" \
    --catalog "${EXPANDED_CATALOG}" \
    --sqlite-journal DELETE \
    --llm-slots 1792 \
    --repository-slots 500 \
    --max-attempts 3 \
    "${RECIPE_ARGS[@]}"
fi

if [[ ! -s "${SCICODEPILE_CATALOG}" ]]; then
  echo "SciCodePile catalog is absent or empty: ${SCICODEPILE_CATALOG}" >&2
  exit 2
fi

# The controller polls METRICS_URL every 30 seconds. It estimates other users'
# traffic as deployment_active - this_process_active, then admits between 500
# and 1792 calls. In-process slots avoid a SQLite write per request. Run only
# one controller per v2 queue; external worker mode retains durable DB slots.
exec "${PYTHON_BIN}" -m factory.reasoning.batch auto \
  --output-root "${OUTPUT_ROOT}" \
  --cache-root "${CACHE_ROOT}" \
  --db "${OUTPUT_ROOT}/batch.sqlite3" \
  --sqlite-journal DELETE \
  --target-sft-rows 10000 \
  --workers 500 \
  --repository-slots 500 \
  --llm-slots 1792 \
  --llm-min-slots 500 \
  --llm-metrics-url "${METRICS_URL}" \
  --metrics-poll-seconds 30 \
  --metrics-timeout 5 \
  --repository-source scicodepile \
  --scicodepile-catalog "${SCICODEPILE_CATALOG}" \
  --repository-limit 19559 \
  --job-lease-seconds 28800 \
  --max-attempts 3 \
  "${RECIPE_ARGS[@]}"
