"""Reference implementation for auto-000002 periodic advection."""

from __future__ import annotations

import numpy as np


def upwind_flux_difference(q: np.ndarray, velocity: float, dx: float) -> np.ndarray:
    """Return the sign-aware first-order upwind derivative -d(a q)/dx."""
    q_arr = np.asarray(q, dtype=float)
    a = float(velocity)
    if a >= 0.0:
        return -a * (q_arr - np.roll(q_arr, 1)) / float(dx)
    return -a * (np.roll(q_arr, -1) - q_arr) / float(dx)


def lax_wendroff_step(q: np.ndarray, velocity: float, dx: float, dt: float) -> np.ndarray:
    """Advance one sign-aware Lax-Wendroff step with periodic neighbors."""
    q_arr = np.asarray(q, dtype=float)
    right = np.roll(q_arr, -1)
    left = np.roll(q_arr, 1)
    laplacian = right - 2.0 * q_arr + left
    courant = float(velocity) * float(dt) / float(dx)
    if courant >= 0.0:
        anti_diffusion = -0.5 * courant * (1.0 - courant) * laplacian
    else:
        anti_diffusion = 0.5 * courant * (1.0 + courant) * laplacian
    return q_arr + float(dt) * upwind_flux_difference(q_arr, float(velocity), float(dx)) + anti_diffusion


def _periodic_linear_interp(q: np.ndarray, x: np.ndarray, shift: float) -> np.ndarray:
    x_arr = np.asarray(x, dtype=float)
    q_arr = np.asarray(q, dtype=float)
    dx = float(x_arr[1] - x_arr[0])
    length = float(x_arr[-1] - x_arr[0] + dx)
    source = (x_arr - float(shift)) % length
    cell = np.floor(source / dx).astype(int) % q_arr.size
    frac = source / dx - np.floor(source / dx)
    next_cell = (cell + 1) % q_arr.size
    return q_arr[cell] * (1.0 - frac) + q_arr[next_cell] * frac


def integrate_periodic_advection(
    q0: np.ndarray,
    x: np.ndarray,
    velocity: float,
    t_final: float,
    steps: int,
) -> tuple[np.ndarray, float, float, float]:
    """Integrate periodic advection and return state plus diagnostics."""
    q = np.array(q0, dtype=float, copy=True)
    x_arr = np.asarray(x, dtype=float)
    dx = float(x_arr[1] - x_arr[0])
    dt = float(t_final) / int(steps)
    for _ in range(int(steps)):
        q = lax_wendroff_step(q, float(velocity), dx, dt)
    exact = _periodic_linear_interp(q0, x_arr, float(velocity) * float(t_final))
    diff = np.abs(q - exact)
    l1_error = float(dx * np.mean(diff))
    max_error = float(np.max(diff))
    cfl = float(abs(float(velocity)) * dt / dx)
    return q, l1_error, max_error, cfl
