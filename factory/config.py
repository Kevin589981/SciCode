"""Central configuration for scicode-factory.

Threshold semantics are borrowed from ScienceInfra factory/config.py
(demo/envs/<env>/factory): FLOOR_MAX, MARGIN_MIN, timeouts and dedup limits.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# --- data locations -------------------------------------------------------
DATA_DIR = Path(os.environ.get("SCICODE_DATA_DIR", "/root/ScienceIDE-workspace/scicode-data"))
VALIDATION_JSONL = DATA_DIR / "validation.jsonl"
TEST_H5 = Path(os.environ.get("SCICODE_TEST_H5", str(DATA_DIR / "gdrive" / "test_data.h5")))

# Directory of the scicode package's src/ tree, prepended to sys.path in the
# assembled test scripts (empty string skips the line; scicode must then be
# importable in PYTHON's environment, e.g. pip-installed).
SCICODE_SRC = os.environ.get("SCICODE_SRC", "")

# Python interpreter used to run assembled scripts. Defaults to the current one.
PYTHON = os.environ.get("SCICODE_FACTORY_PYTHON", sys.executable)

WORK_DIR = Path(os.environ.get("SCICODE_FACTORY_WORK", ".work"))

# --- funnel thresholds (ScienceInfra config.py equivalents) ---------------
# Defective-baseline score above this leaves no reward headroom -> "floor_high".
FLOOR_MAX = float(os.environ.get("SCICODE_FLOOR_MAX", "0.65"))
# Per-step script wall-time budget (SciCode official harness uses 1800 s).
STEP_TIMEOUT_SEC = int(os.environ.get("SCICODE_STEP_TIMEOUT", "600"))
# Wall-time above this marks a step "heavy": skipped in double-run determinism
# calibration and in reference wall estimation repeats.
HEAVY_STEP_WALL_SEC = 120.0
# Max injection sites emitted per (operator family, step); avoids quadratic
# candidate blowup on literal-dense functions.
MAX_SITES_PER_FAMILY = int(os.environ.get("SCICODE_MAX_SITES", "2"))

N_WORKERS = int(os.environ.get("SCICODE_FACTORY_WORKERS", "8"))

# Steps whose gold code is known to fail on this machine through NO fault of
# the pipeline: 70.8 is BLAS/LAPACK float drift (verified with high-precision
# arithmetic during dataset prep; see scicode-data/scripts/), 78.3 is a
# numpy-2.x broadcasting incompatibility in the upstream gold code
# (ValueError: (10001,2) vs (2,2) in the official test script itself).
ENV_SKIP_STEPS = {"70.8", "78.3"}
