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
  pipeline)
    : "${SCICODE_LLM_API_KEY:?Set the solver key externally}"
    : "${SCICODE_REVIEW_BASE_URL:?Set the separate reviewer endpoint}"
    : "${SCICODE_REVIEW_API_KEY:?Set the separate reviewer key externally}"
    root="${SCICODE_DATA_ROOT:-$repo_root/data-reasoning-deepseek-supported4586-v1}"
    reviewer="${SCICODE_REVIEW_MODEL:-Kimi-K3}"
    workers="${SCICODE_REVIEW_WORKERS:-64}"
    # Fresh full pipeline only. After interruption, resume the relevant stage
    # explicitly rather than silently reviewing an outdated native export.
    test ! -e "$root/native-v1"
    test ! -e "$root/reviewed-v1"
    "$python_bin" -m factory.reasoning.distill validate --inputs "$inputs" --config "$config"
    "$python_bin" -m factory.reasoning.distill run --inputs "$inputs" --config "$config" --output "$output"
    "$python_bin" -m factory.reasoning.distill export --inputs "$inputs" --output "$output" --out-dir "$root/native-v1"
    "$python_bin" -m factory.reasoning.distill audit --native "$root/native-v1" --out "$root/scientific-audit.jsonl" --reviewer "$reviewer" --workers "$workers"
    "$python_bin" -m factory.reasoning.distill grade --inputs "$inputs" --native "$root/native-v1" --out "$root/reasoning-quality.jsonl" --reviewer "$reviewer" --workers "$workers"
    "$python_bin" -m factory.reasoning.distill select --native "$root/native-v1" --audit "$root/scientific-audit.jsonl" --grades "$root/reasoning-quality.jsonl" --reviewer "$reviewer" --out-dir "$root/reviewed-v1"
    ;;
  *)
    echo "Usage: $0 [validate|run|pipeline]; default validate makes no model requests" >&2
    exit 2
    ;;
esac
