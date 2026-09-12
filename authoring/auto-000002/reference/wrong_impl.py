"""Deliberately wrong implementation for oracle independence checks."""

from __future__ import annotations

import numpy as np


def upwind_flux_difference(q: np.ndarray, velocity: float, dx: float) -> np.ndarray:
    q_arr = np.asarray(q, dtype=float)
    flux = float(velocity) * q_arr
    # Wrong: fixed left-neighbor flux is not upwind when velocity is negative.
    left = np.roll(flux, 1)
    return -(flux - left) / float(dx)


def lax_wendroff_step(q: np.ndarray, velocity: float, dx: float, dt: float) -> np.ndarray:
    q_arr = np.asarray(q, dtype=float)
    right = np.roll(q_arr, -1)
    left = np.roll(q_arr, 1)
    laplacian = right - 2.0 * q_arr + left
    # Wrong: applies the positive-velocity anti-diffusion correction for all signs.
    courant = float(velocity) * float(dt) / float(dx)
    anti_diffusion = -0.5 * courant * (1.0 - courant) * laplacian
    return q_arr + float(dt) * upwind_flux_difference(q_arr, float(velocity), float(dx)) + anti_diffusion


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
