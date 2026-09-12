"""Deliberately wrong implementation for oracle independence checks."""

from __future__ import annotations

import numpy as np


def upwind_flux_difference(q: np.ndarray, velocity: float, dx: float) -> np.ndarray:
    q_arr = np.asarray(q, dtype=float)
    flux = velocity * q_arr
    # Wrong: zero-gradient boundaries instead of periodic wrap.
    left = flux.copy()
    left[1:] = flux[:-1]
    return -(flux - left) / float(dx)


def lax_wendroff_step(q: np.ndarray, velocity: float, dx: float, dt: float) -> np.ndarray:
    q_arr = np.asarray(q, dtype=float)
    # Wrong: first-order upwind Euler, not Lax-Wendroff.
    c = float(velocity) * float(dt) / float(dx)
    left = np.roll(q_arr, 1)
    return q_arr - c * (q_arr - left)


def integrate_periodic_advection(
    q0: np.ndarray,
    x: np.ndarray,
    velocity: float,
    t_final: float,
    steps: int,
) -> tuple[np.ndarray, float, float, float]:
    q = np.array(q0, dtype=float, copy=True)
    x_arr = np.asarray(x, dtype=float)
    dx = float(x_arr[1] - x_arr[0])
    dt = float(t_final) / int(steps)
    for _ in range(int(steps)):
        q = lax_wendroff_step(q, float(velocity), dx, dt)
    length = float(x_arr[-1] - x_arr[0] + dx)
    source = (x_arr - float(velocity) * float(t_final)) % length
    cell = np.floor(source / dx).astype(int) % q.size
    frac = source / dx - np.floor(source / dx)
    exact = q0[cell] * (1.0 - frac) + q0[(cell + 1) % q.size] * frac
    err = np.abs(q - exact)
    return q, float(dx * np.mean(err)), float(np.max(err)), float(abs(float(velocity)) * dt / dx)
