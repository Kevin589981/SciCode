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
tasks_per_repo=3..16
repository_limit=400
repository_source=SciCodePile_clean_dataset:360 + keyword_search:40
prerequisite: prepare and validate the cleaned SciCodePile snapshot catalog
Run: bash factory/reasoning/run_1k_kimi.sh --execute
EOF
  exit 0
fi

REPO_ROOT="${SCICODE_REPO_ROOT:-/root/ScienceIDE-workspace/SciCode}"
OUTPUT_ROOT="${SCICODE_OUTPUT_ROOT:-${REPO_ROOT}/data-reasoning-1k-pilot-v1}"
CACHE_ROOT="${SCICODE_CACHE_ROOT:-${REPO_ROOT}/.cache/reasoning-repositories}"
PYTHON_BIN="${SCICODE_FACTORY_PYTHON:-/root/scicode-factory-venv/bin/python}"
METRICS_URL="${SCICODE_LLM_METRICS_URL:-http://10.100.184.127:29000/metrics}"
MODEL="${SCICODE_LLM_MODEL:-Kimi-K3}"
SCICODEPILE_CATALOG="${SCICODEPILE_CATALOG:-${REPO_ROOT}/.cache/scicodepile/catalog.jsonl}"

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
  --repository-source hybrid \
  --scicodepile-catalog "${SCICODEPILE_CATALOG}" \
  --keyword-channel-limit 40 \
  --min-stars 5 \
  --search-scope name,description \
  --pages-per-query 3 \
  --per-page 100 \
  --repository-limit 400 \
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
