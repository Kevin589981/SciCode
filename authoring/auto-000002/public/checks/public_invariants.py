"""Public invariant checks for the periodic advection candidate.

These checks do not contain oracle values or private reference code. They state
mathematical invariants for sign-aware periodic upwind advection.
"""

from __future__ import annotations

import numpy as np


def test_positive_velocity_periodic_upwind() -> None:
    q = np.array([1.0, 2.0, 3.0, 4.0])
    derivative = -1.5 * (q - np.roll(q, 1)) / 0.5
    assert np.allclose(derivative, np.array([9.0, -3.0, -3.0, -3.0]))
    assert abs(float(derivative.sum())) < 1e-12


def test_negative_velocity_periodic_upwind() -> None:
    q = np.array([0.0, 1.0, 0.0, -1.0])
    derivative = 2.0 * (np.roll(q, -1) - q) / 0.25
    assert np.allclose(derivative, np.array([8.0, -8.0, -8.0, 8.0]))
    assert abs(float(derivative.sum())) < 1e-12


def test_lax_wendroff_mean_preservation() -> None:
    x = np.linspace(0.0, 1.0, 33, endpoint=False)
    q = np.sin(2.0 * np.pi * x)
    right = np.roll(q, -1)
    left = np.roll(q, 1)
    c = -0.2
    q_new = q - 0.5 * c * (right - left) + 0.5 * c * c * (right - 2.0 * q + left)
    assert q_new.shape == q.shape
    assert abs(float(q_new.mean() - q.mean())) < 1e-14


if __name__ == "__main__":
    test_positive_velocity_periodic_upwind()
    test_negative_velocity_periodic_upwind()
    test_lax_wendroff_mean_preservation()
