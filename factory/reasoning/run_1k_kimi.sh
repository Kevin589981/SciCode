#!/usr/bin/env bash
set -euo pipefail

# Bounded pilot launcher. It is intentionally inert without --execute.
if [[ "${1:-}" != "--execute" ]]; then
  cat <<'EOF'
Dry configuration only; no batch was started.
target_sft_rows=1000
workers=200
llm_concurrency=200
context_window_tokens=262144
author_max_tokens=131072
solver_max_tokens=196608
verifier_max_tokens=65536
tasks_per_repo=3..16
repository_limit=3600
repository_source=SciCodePile_clean_dataset_only
prerequisite: prepare and validate the cleaned SciCodePile snapshot catalog
Run: bash factory/reasoning/run_1k_kimi.sh --execute
EOF
  exit 0
fi

REPO_ROOT="${SCICODE_REPO_ROOT:-/root/ScienceIDE-workspace/SciCode-v3}"
DATA_ROOT="${SCICODE_DATA_ROOT:-/root/ScienceIDE-workspace/SciCode}"
OUTPUT_ROOT="${SCICODE_OUTPUT_ROOT:-${REPO_ROOT}/data-reasoning-1k-pilot-v4}"
CACHE_ROOT="${SCICODE_CACHE_ROOT:-${DATA_ROOT}/.cache/reasoning-repositories}"
PYTHON_BIN="${SCICODE_FACTORY_PYTHON:-/root/scicode-factory-venv/bin/python}"
METRICS_URL="${SCICODE_LLM_METRICS_URL:-http://10.100.184.127:29000/metrics}"
MODEL="${SCICODE_LLM_MODEL:-Kimi-K3}"
SCICODEPILE_CATALOG="${SCICODEPILE_CATALOG:-${DATA_ROOT}/.cache/scicodepile/catalog.jsonl}"

export SCICODE_LLM_BASE_URL="${SCICODE_LLM_BASE_URL:-http://10.100.184.127:5050/v1}"
export SCICODE_LLM_API_KEY="${SCICODE_LLM_API_KEY:-dummy}"
export SCICODE_LLM_MODEL="${MODEL}"
export HTTP_PROXY="${HTTP_PROXY:-http://httpproxy-headless.kubebrain.svc.lg.shzhisuan.local:3128}"
export HTTPS_PROXY="${HTTPS_PROXY:-${HTTP_PROXY}}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost,10.100.184.127}"

if [[ ! -s "${SCICODEPILE_CATALOG}" ]]; then
  echo "SciCodePile catalog is absent or empty: ${SCICODEPILE_CATALOG}" >&2
  exit 2
fi

cd "${REPO_ROOT}"

exec "${PYTHON_BIN}" -m factory.reasoning.batch auto \
  --output-root "${OUTPUT_ROOT}" \
  --cache-root "${CACHE_ROOT}" \
  --db "${OUTPUT_ROOT}/batch.sqlite3" \
  --sqlite-journal DELETE \
  --target-sft-rows 1000 \
  --workers 200 \
  --repository-slots 200 \
  --llm-slots 200 \
  --llm-min-slots 200 \
  --llm-metrics-url "${METRICS_URL}" \
  --metrics-poll-seconds 30 \
  --metrics-timeout 5 \
  --repository-source scicodepile \
  --scicodepile-catalog "${SCICODEPILE_CATALOG}" \
  --repository-limit 3600 \
  --tasks-per-repo 16 \
  --min-tasks-per-repo 3 \
  --reservation-rows-per-repo 5 \
  --max-mined-candidates 1200 \
  --profile-model "${MODEL}" \
  --author-model "${MODEL}" \
  --critic-model "${MODEL}" \
  --verifier-model "${MODEL}" \
  --solver-model "${MODEL}" \
  --judge-model "${MODEL}" \
  --context-window-tokens 262144 \
  --author-max-tokens 131072 \
  --solver-max-tokens 196608 \
  --critic-max-tokens 16384 \
  --verifier-max-tokens 65536 \
  --judge-max-tokens 32768 \
  --judge-max-input-chars 900000 \
  --timeout 7200 \
  --pipeline-concurrency 4 \
  --job-lease-seconds 28800 \
  --max-attempts 3
