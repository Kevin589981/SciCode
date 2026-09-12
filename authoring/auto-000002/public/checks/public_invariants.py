"""Public invariant checks for the periodic advection candidate.

These checks do not contain oracle values or private reference code. They only
state mathematical invariants that any correct periodic finite-volume advection
implementation should satisfy.
"""

from __future__ import annotations

import numpy as np


def test_periodic_flux_difference_conservative() -> None:
    q = np.array([1.0, 2.0, 3.0, 4.0])
    velocity = 1.5
    dx = 0.5
    flux = velocity * q
    expected_derivative = -(flux - np.roll(flux, 1)) / dx
    assert abs(float(expected_derivative.sum())) < 1e-12
    assert np.allclose(expected_derivative, np.array([9.0, -3.0, -3.0, -3.0]))


def test_lax_wendroff_translation_shape() -> None:
    x = np.linspace(0.0, 1.0, 33, endpoint=False)
    q = np.sin(2.0 * np.pi * x)
    assert q.shape == (33,)
    assert abs(float(np.mean(q))) < 1e-12


if __name__ == "__main__":
    test_periodic_flux_difference_conservative()
    test_lax_wendroff_translation_shape()
