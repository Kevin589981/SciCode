#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${SCICODE_PYTHON:-/root/scicode-factory-venv/bin/python}"
root="${SCICODE_DATA_ROOT:-$repo_root/data-reasoning-deepseek-supported4586-v1}"
inputs="${SCICODE_INPUTS:-$root/inputs}"
output="${SCICODE_OUTPUT:-$root/solver}"
config="${SCICODE_CONFIG:-$repo_root/factory/reasoning/configs/deepseek_v4flash_0731_reuse_4586.json}"
cd "$repo_root"
case "${1:-validate}" in
  validate)
    exec "$python_bin" -m factory.reasoning.distill validate --inputs "$inputs" --config "$config"
    ;;
  run)
    : "${SCICODE_LLM_API_KEY:?Set the solver key externally; do not put it in Git}"
    exec "$python_bin" -m factory.reasoning.distill run --inputs "$inputs" --config "$config" --output "$output"
    ;;
  pipeline)
    : "${SCICODE_LLM_API_KEY:?Set the solver key externally}"
    : "${SCICODE_REVIEW_BASE_URL:?Set the separate reviewer endpoint}"
    : "${SCICODE_REVIEW_API_KEY:?Set the separate reviewer key externally}"
    reviewer="${SCICODE_REVIEW_MODEL:-Kimi-K3}"
    workers="${SCICODE_REVIEW_WORKERS:-500}"
    exec "$python_bin" -m factory.reasoning.stream_distill \
      --inputs "$inputs" --output "$output" --root "$root" --config "$config" \
      --reviewer "$reviewer" --workers "$workers" \
      --context-window-tokens "${SCICODE_REVIEW_CONTEXT_WINDOW:-262144}" \
      --max-tokens "${SCICODE_REVIEW_MAX_TOKENS:-65536}"
    ;;
  *)
    echo "Usage: $0 [validate|run|pipeline]; default validate makes no model requests" >&2
    exit 2
    ;;
esac
