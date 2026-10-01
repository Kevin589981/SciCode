#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export SCICODE_DATA_ROOT="${SCICODE_DATA_ROOT:-$repo_root/data-reasoning-deepseek-complement4854-20261002-v1}"
export SCICODE_CONFIG="${SCICODE_CONFIG:-$repo_root/factory/reasoning/configs/deepseek_v4flash_0731_reuse_complement_20261002.json}"
exec bash "$repo_root/factory/reasoning/run_4586_deepseek_reuse.sh" "${1:-validate}"
