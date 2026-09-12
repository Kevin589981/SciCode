#!/usr/bin/env python3
"""Verify reference, independent, and wrong implementations against oracle tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PROBLEM = ROOT / "public" / "problem.jsonl"
ORACLE = ROOT / "oracle" / "targets.h5"


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_targets(step_id: str, count: int) -> list[object]:
    import h5py
    from scicode.parse.parse import process_hdf5_to_tuple

    with h5py.File(ORACLE, "r") as handle:
        return process_hdf5_to_tuple(step_id, count, str(ORACLE))


def run_impl(module: ModuleType, label: str) -> dict[str, object]:
    row = json.loads(PROBLEM.read_text(encoding="utf-8"))
    results: list[dict[str, object]] = []
    for step in row["sub_steps"]:
        step_id = str(step["step_number"])
        targets = load_targets(step_id, len(step["test_cases"]))
        step_ok = True
        failures: list[str] = []
        for index, case in enumerate(step["test_cases"]):
            namespace = {
                "np": np,
                "upwind_flux_difference": module.upwind_flux_difference,
                "lax_wendroff_step": module.lax_wendroff_step,
                "integrate_periodic_advection": module.integrate_periodic_advection,
                "target": targets[index],
            }
            try:
                exec(compile(case, f"{label}:{step_id}:test{index + 1}", "exec"), namespace, namespace)
            except Exception as exc:  # noqa: BLE001 - verifier must record any failure.
                step_ok = False
                failures.append(f"test{index + 1}: {type(exc).__name__}: {exc}")
        results.append({"step_number": step_id, "passed": step_ok, "failures": failures})
    return {"implementation": label, "all_passed": all(item["passed"] for item in results), "steps": results}


def main() -> int:
    reference = load_module("reference_impl", ROOT / "reference" / "reference_impl.py")
    independent = load_module("independent_impl", ROOT / "reference" / "independent_impl.py")
    wrong = load_module("wrong_impl", ROOT / "reference" / "wrong_impl.py")
    report = {
        "status": "ok",
        "implementations": [
            run_impl(reference, "reference"),
            run_impl(independent, "independent"),
            run_impl(wrong, "wrong"),
        ],
    }
    if not report["implementations"][0]["all_passed"]:
        report["status"] = "failed"
    if not report["implementations"][1]["all_passed"]:
        report["status"] = "failed"
    if report["implementations"][2]["all_passed"]:
        report["status"] = "failed"
        report["wrong_implementation_unexpectedly_passed"] = True
    (ROOT / "validation" / "oracle_semantic_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
