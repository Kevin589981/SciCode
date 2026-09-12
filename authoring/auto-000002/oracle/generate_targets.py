#!/usr/bin/env python3
"""Generate private oracle targets for auto-000002."""

from __future__ import annotations

import sys
from pathlib import Path

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "reference") not in sys.path:
    sys.path.insert(0, str(ROOT / "reference"))

from reference_impl import (  # noqa: E402
    integrate_periodic_advection,
    lax_wendroff_step,
    upwind_flux_difference,
)


def save_group(handle: h5py.File, step_id: str, values: list[object]) -> None:
    if step_id in handle:
        del handle[step_id]
    group = handle.create_group(step_id)
    for index, value in enumerate(values, start=1):
        test_group = group.create_group(f"test{index}")
        if isinstance(value, tuple):
            for var_index, item in enumerate(value, start=1):
                test_group.create_dataset(f"var{var_index}", data=np.asarray(item))
        else:
            test_group.create_dataset("var1", data=np.asarray(value))


def main() -> None:
    oracle = ROOT / "oracle"
    oracle.mkdir(parents=True, exist_ok=True)
    targets = oracle / "targets.h5"
    with h5py.File(targets, "w") as handle:
        save_group(
            handle,
            "1",
            [
                (upwind_flux_difference(np.array([1.0, 2.0, 3.0, 4.0]), 1.5, 0.5),),
                (upwind_flux_difference(np.array([0.0, 1.0, 0.0, -1.0]), -2.0, 0.25),),
                (upwind_flux_difference(np.array([2.0, -1.0, 0.5, 3.0]), 0.75, 0.1),),
            ],
        )
        save_group(
            handle,
            "2",
            [
                lax_wendroff_step(np.array([0.0, 1.0, 0.0, -1.0]), 1.0, 1.0, 0.25),
                lax_wendroff_step(np.sin(np.linspace(0.0, 2.0 * np.pi, 9, endpoint=False)), -0.4, 0.2, 0.1),
                lax_wendroff_step(np.array([1.0, 0.0, -1.0, 2.0, 0.5]), 0.6, 0.25, 0.05),
            ],
        )
        x1 = np.linspace(0.0, 1.0, 65, endpoint=False)
        q1 = np.sin(2.0 * np.pi * x1) + 0.25 * np.cos(4.0 * np.pi * x1)
        x2 = np.linspace(0.0, 2.0, 81, endpoint=False)
        q2 = 0.5 + 0.25 * np.sin(np.pi * x2)
        x3 = np.linspace(0.0, 1.5, 97, endpoint=False)
        q3 = np.exp(-((x3 - 0.35) ** 2) / 0.02) + 0.1 * np.cos(6.0 * np.pi * x3)
        save_group(
            handle,
            "3",
            [
                integrate_periodic_advection(q1, x1, 0.2, 0.37, 200),
                integrate_periodic_advection(q2, x2, -0.3, 0.6, 150),
                integrate_periodic_advection(q3, x3, 0.15, 0.45, 180),
            ],
        )
    print(targets)


if __name__ == "__main__":
    main()
