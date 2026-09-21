#!/usr/bin/env bash
set -euo pipefail

# Prepared production launcher. It is intentionally inert without --execute.
if [[ "${1:-}" != "--execute" ]]; then
  cat <<'EOF'
Dry configuration only; no batch was started.
target_sft_rows=10000
workers=500
dynamic_llm_concurrency=500..1792
context_window_tokens=262144
tasks_per_repo=3..16
repository_limit=4000
Run: bash factory/reasoning/run_10k_kimi.sh --execute
EOF
  exit 0
fi

REPO_ROOT="${SCICODE_REPO_ROOT:-/root/ScienceIDE-workspace/SciCode}"
OUTPUT_ROOT="${SCICODE_OUTPUT_ROOT:-${REPO_ROOT}/data-reasoning-10k-v1}"
CACHE_ROOT="${SCICODE_CACHE_ROOT:-${REPO_ROOT}/.cache/reasoning-repositories}"
PYTHON_BIN="${SCICODE_FACTORY_PYTHON:-/root/scicode-factory-venv/bin/python}"
METRICS_URL="${SCICODE_LLM_METRICS_URL:-http://10.100.184.127:29000/metrics}"
MODEL="${SCICODE_LLM_MODEL:-Kimi-K3}"

export SCICODE_LLM_BASE_URL="${SCICODE_LLM_BASE_URL:-http://10.100.184.127:5050/v1}"
export SCICODE_LLM_API_KEY="${SCICODE_LLM_API_KEY:-dummy}"
export SCICODE_LLM_MODEL="${MODEL}"
export HTTP_PROXY="${HTTP_PROXY:-http://httpproxy-headless.kubebrain.svc.lg.shzhisuan.local:3128}"
export HTTPS_PROXY="${HTTPS_PROXY:-${HTTP_PROXY}}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost,10.100.184.127}"

cd "${REPO_ROOT}"

# The controller polls METRICS_URL every 30 seconds. It estimates other users'
# traffic as deployment_active - this_queue_active, then admits between 500 and
# 1792 local calls. SQLite leases keep this bound global across all local workers.
exec "${PYTHON_BIN}" -m factory.reasoning.batch auto \
  --output-root "${OUTPUT_ROOT}" \
  --cache-root "${CACHE_ROOT}" \
  --db "${OUTPUT_ROOT}/batch.sqlite3" \
  --sqlite-journal WAL \
  --target-sft-rows 10000 \
  --workers 500 \
  --repository-slots 500 \
  --llm-slots 1792 \
  --llm-min-slots 500 \
  --llm-metrics-url "${METRICS_URL}" \
  --metrics-poll-seconds 30 \
  --metrics-timeout 5 \
  --min-stars 5 \
  --search-scope name,description \
  --pages-per-query 3 \
  --per-page 100 \
  --repository-limit 4000 \
  --request-interval 2.1 \
  --expand-keywords \
  --max-expanded 40 \
  --expansion-model "${MODEL}" \
  --tasks-per-repo 16 \
  --min-tasks-per-repo 3 \
  --max-mined-candidates 1200 \
  --profile-model "${MODEL}" \
  --author-model "${MODEL}" \
  --critic-model "${MODEL}" \
  --verifier-model "${MODEL}" \
  --solver-model "${MODEL}" \
  --judge-model "${MODEL}" \
  --context-window-tokens 262144 \
  --max-tokens 65536 \
  --critic-max-tokens 4096 \
  --verifier-max-tokens 4096 \
  --judge-max-tokens 8192 \
  --judge-max-input-chars 900000 \
  --timeout 7200 \
  --pipeline-concurrency 4 \
  --job-lease-seconds 28800 \
  --max-attempts 3
