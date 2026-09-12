#!/usr/bin/env bash
set -euo pipefail

repository_root="${SCICODE_REPOSITORY_ROOT:-/root/scicode-authoring/repository}"
python_bin="${SCICODE_PYTHON:-python3}"

export PYTHONPATH="${repository_root}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec "${python_bin}" "${repository_root}/scripts/run_10k_batch.py" "$@"
