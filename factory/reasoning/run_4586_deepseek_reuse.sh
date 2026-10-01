#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${SCICODE_PYTHON:-/root/scicode-factory-venv/bin/python}"
inputs="${SCICODE_INPUTS:-$repo_root/data-reasoning-deepseek-supported4586-v1/inputs}"
output="${SCICODE_OUTPUT:-$repo_root/data-reasoning-deepseek-supported4586-v1/solver}"
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
  *)
    echo "Usage: $0 [validate|run]; default validate makes no model requests" >&2
    exit 2
    ;;
esac
