#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--execute" || ! "${2:-}" =~ ^[0-9]+$ ]]; then
  cat <<'EOF'
Wait for one SciCodePile preparation process, validate all 3,600 snapshots, and
then replace this process with the bounded 1k/200 Kimi pilot.
Usage: bash factory/reasoning/launch_1k_after_prepare.sh --execute PREPARER_PID
EOF
  exit 0
fi

PREPARER_PID="$2"
REPO_ROOT="${SCICODE_REPO_ROOT:-/root/ScienceIDE-workspace/SciCode}"
PYTHON_BIN="${SCICODE_FACTORY_PYTHON:-/root/scicode-factory-venv/bin/python}"
CATALOG="${SCICODEPILE_CATALOG:-${REPO_ROOT}/.cache/scicodepile/catalog.jsonl}"
OUTPUT_ROOT="${SCICODE_OUTPUT_ROOT:-${REPO_ROOT}/data-reasoning-1k-pilot-v1}"

if [[ -r "/proc/${PREPARER_PID}/cmdline" ]]; then
  command_line="$(tr '\0' ' ' < "/proc/${PREPARER_PID}/cmdline")"
  if [[ "${command_line}" != *"factory.reasoning.scicodepile_dataset"* ]]; then
    echo "PID ${PREPARER_PID} is not the SciCodePile preparer" >&2
    exit 2
  fi
  # GNU tail blocks on the process lifecycle without a polling shell loop.
  tail --pid="${PREPARER_PID}" -f /dev/null || true
fi

cd "${REPO_ROOT}"
"${PYTHON_BIN}" - "${CATALOG}" <<'PY'
import json
import sys
from pathlib import Path

from factory.reasoning.discovery import load_scicodepile_manifest

catalog = Path(sys.argv[1])
report_path = catalog.with_name(catalog.name + ".meta.json")
rows = load_scicodepile_manifest(catalog)
report = json.loads(report_path.read_text(encoding="utf-8"))
if len(rows) != 3600:
    raise SystemExit(f"catalog has {len(rows)} rows, expected 3600")
if report.get("prepared_repositories") != 3600:
    raise SystemExit(
        "preparation report mismatch: "
        f"{report.get('prepared_repositories')!r} prepared repositories"
    )
missing = [
    row["full_name"]
    for row in rows
    if not (Path(row["snapshot_path"]) / ".scicodepile_snapshot.json").is_file()
]
if missing:
    raise SystemExit(f"missing snapshot metadata for {len(missing)} repositories")
print(
    json.dumps(
        {
            "event": "catalog_validated",
            "repositories": len(rows),
            "selection_hash": report.get("selection_hash"),
        }
    ),
    flush=True,
)
PY

if [[ -e "${OUTPUT_ROOT}/batch.sqlite3" ]]; then
  echo "pilot database already exists: ${OUTPUT_ROOT}/batch.sqlite3" >&2
  exit 2
fi
mkdir -p "${OUTPUT_ROOT}"
exec bash factory/reasoning/run_1k_kimi.sh --execute
