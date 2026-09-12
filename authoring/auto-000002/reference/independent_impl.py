"""Independent implementation for auto-000002 periodic advection."""

from __future__ import annotations

import numpy as np


def _left_periodic(values: np.ndarray) -> np.ndarray:
    return np.concatenate(([values[-1]], values[:-1]))


def _right_periodic(values: np.ndarray) -> np.ndarray:
    return np.concatenate((values[1:], [values[0]]))


def upwind_flux_difference(q: np.ndarray, velocity: float, dx: float) -> np.ndarray:
    q_arr = np.asarray(q, dtype=float)
    return (velocity / float(dx)) * (_left_periodic(q_arr) - q_arr)


def lax_wendroff_step(q: np.ndarray, velocity: float, dx: float, dt: float) -> np.ndarray:
    q_arr = np.asarray(q, dtype=float)
    q_l = _left_periodic(q_arr)
    q_r = _right_periodic(q_arr)
    courant = float(velocity) * float(dt) / float(dx)
    return (
        q_arr
        - 0.5 * courant * (q_r - q_l)
        + 0.5 * courant * courant * (q_r - 2.0 * q_arr + q_l)
    )


def integrate_periodic_advection(
    q0: np.ndarray,
    x: np.ndarray,
    velocity: float,
    t_final: float,
    steps: int,
) -> tuple[np.ndarray, float, float, float]:
    q = np.asarray(q0, dtype=float).copy()
    x_arr = np.asarray(x, dtype=float)
    dx = float(x_arr[1] - x_arr[0])
    dt = float(t_final) / int(steps)
    for _ in range(int(steps)):
        q = lax_wendroff_step(q, float(velocity), dx, dt)
    length = float(x_arr[-1] - x_arr[0] + dx)
    shift = float(velocity) * float(t_final)
    source = (x_arr - shift) % length
    index = np.floor(source / dx).astype(int)
    frac = source / dx - index
    index %= q.size
    exact = (1.0 - frac) * q0[index] + frac * q0[(index + 1) % q.size]
    err = np.abs(q - exact)
    return q, float(dx * np.mean(err)), float(np.max(err)), float(abs(float(velocity)) * dt / dx)
