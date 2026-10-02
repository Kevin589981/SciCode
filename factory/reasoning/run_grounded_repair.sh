#!/usr/bin/env bash
set -euo pipefail
WORKTREE=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$WORKTREE"
: "${SCICODE_LLM_API_KEY:?set the DeepSeek teacher key outside Git}"
: "${SCICODE_REVIEW_API_KEY:?set the Kimi reviewer key outside Git}"
export SCICODE_LLM_BASE_URL=${SCICODE_LLM_BASE_URL:-http://10.100.184.69:4000/v1}
export SCICODE_LLM_MODEL=DeepSeek-V4-Flash-0731
export SCICODE_REVIEW_BASE_URL=${SCICODE_REVIEW_BASE_URL:-http://10.100.184.69:4000/v1}
export NO_PROXY="${NO_PROXY:-},10.100.184.69,localhost,127.0.0.1"
export no_proxy="$NO_PROXY"
PYTHON=${SCICODE_PYTHON:-/root/scicode-factory-venv/bin/python}
OUTPUT=${SCICODE_REPAIR_OUTPUT:-$WORKTREE/data-reasoning-grounded-audit-repair-20261002-v2}
umask 077
ulimit -n 16384
if [[ ! -f "$OUTPUT/inputs/manifest.json" ]]; then
    "$PYTHON" -u -m factory.reasoning.reaudit_repair prepare --inputs "$OUTPUT/inputs" --sources \
        /root/ScienceIDE-workspace/SciCode-deepseek-recovery-20261001/data-reasoning-deepseek-supported4586-recovery-v2 \
        /root/ScienceIDE-workspace/SciCode-deepseek-complement-20261002/data-reasoning-deepseek-complement4854-20261002-v1
fi
exec "$PYTHON" -u -m factory.reasoning.reaudit_repair run --inputs "$OUTPUT/inputs" --root "$OUTPUT" \
    --config factory/reasoning/configs/deepseek_grounded_repair_20261002.json \
    --review-workers 500 --generation-workers 500 --repair-rounds 2 --recovery-passes 3 "$@"
