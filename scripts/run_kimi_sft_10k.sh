#!/usr/bin/env bash
# Launch the 10k SciCode SFT generator on the YiDian development machine.
# Keep this launcher free of credentials. Set KIMI_API_KEY only if the endpoint
# requires authentication; the current internal Kimi endpoint accepts no key.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/root/scicode-avacore/AvaCore/.venv/bin/python}"
RUN_ROOT="${RUN_ROOT:-/root/scicode-avacore/runs}"
RUN_STAMP="${RUN_STAMP:-$(date +%Y%m%d-%H%M%S)}"
OUTPUT="${OUTPUT:-$RUN_ROOT/scicode-kimi-sft-10000-$RUN_STAMP.jsonl}"

MODEL_SERVER_URL="${MODEL_SERVER_URL:-http://172.17.52.41:5050}"
MODEL_NAME="${MODEL_NAME:-Kimi-K3}"
METRICS_URL="${METRICS_URL:-http://172.17.52.41:29000/metrics}"
TARGET_RECORDS="${TARGET_RECORDS:-10000}"
MIN_CONCURRENCY="${MIN_CONCURRENCY:-500}"
MAX_CONCURRENCY="${MAX_CONCURRENCY:-1536}"
MAX_TOKENS="${MAX_TOKENS:-65536}"
CONTEXT_LENGTH="${CONTEXT_LENGTH:-1048576}"
REQUEST_TIMEOUT="${REQUEST_TIMEOUT:-7200}"
RETRIES="${RETRIES:-2}"

if [[ -e "$OUTPUT" ]]; then
  printf 'Refusing to overwrite existing output: %s\n' "$OUTPUT" >&2
  exit 2
fi

# 1536 network requests need more descriptors than the common 1024 soft limit.
if ulimit -n 8192 2>/dev/null; then
  :
else
  printf 'Warning: could not raise the file-descriptor limit; current limit is ' >&2
  ulimit -n >&2 || true
fi

cd "$REPO_ROOT"
export PYTHONUNBUFFERED=1
export HF_DATASETS_OFFLINE="${HF_DATASETS_OFFLINE:-1}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"

args=(
  scripts/generate_kimi_scicode_sft.py
  --base-url "$MODEL_SERVER_URL"
  --model "$MODEL_NAME"
  --metrics-url "$METRICS_URL"
  --output "$OUTPUT"
  --target-records "$TARGET_RECORDS"
  --min-concurrency "$MIN_CONCURRENCY"
  --max-concurrency "$MAX_CONCURRENCY"
  --temperature 0.6
  --reasoning-effort max
  --max-tokens "$MAX_TOKENS"
  --context-length "$CONTEXT_LENGTH"
  --timeout "$REQUEST_TIMEOUT"
  --retries "$RETRIES"
  --stream
)
if [[ -n "${KIMI_API_KEY:-}" ]]; then
  args+=(--api-key "$KIMI_API_KEY")
fi
if [[ "${USE_PROXY:-0}" == "1" ]]; then
  args+=(--trust-env)
fi

printf 'Starting SciCode SFT generation\n'
printf 'output=%s model=%s target=%s concurrency=%s..%s\n' \
  "$OUTPUT" "$MODEL_NAME" "$TARGET_RECORDS" "$MIN_CONCURRENCY" "$MAX_CONCURRENCY"
exec "$PYTHON_BIN" "${args[@]}"

