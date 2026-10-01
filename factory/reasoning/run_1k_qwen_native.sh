#!/usr/bin/env bash
set -euo pipefail

# Uses 1,000 audited v1 questions and their exact student-visible prompts.
# Only the Qwen solver response is generated anew; no Kimi answer is copied.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
out_dir="$repo_root/data-reasoning-qwen35-native-1k-v1"
python_bin="${SCICODE_PYTHON:-/root/scicode-factory-venv/bin/python}"

: "${SCICODE_LLM_API_KEY:?Set SCICODE_LLM_API_KEY outside this script}"
export SCICODE_LLM_BASE_URL="${SCICODE_LLM_BASE_URL:-http://10.100.184.69:4000/v1}"
export SCICODE_LLM_MODEL="${SCICODE_LLM_MODEL:-Qwen3.5-35B-A3B}"
export NO_PROXY="10.100.184.69,${NO_PROXY:-}"
export no_proxy="$NO_PROXY"

test -s "$out_dir/tasks.jsonl"
test -s "$out_dir/prompts.jsonl"
cd "$repo_root"
exec "$python_bin" -m factory.reasoning.rollout \
  --tasks "$out_dir/tasks.jsonl" \
  --prompts "$out_dir/prompts.jsonl" \
  --out "$out_dir/traces.jsonl" \
  --model "$SCICODE_LLM_MODEL" \
  --temperature 0.7 \
  --max-tokens 196608 \
  --timeout 2400 \
  --concurrency "${SCICODE_CONCURRENCY:-500}" \
  --run-variant qwen-native-audited-prompt-v1
