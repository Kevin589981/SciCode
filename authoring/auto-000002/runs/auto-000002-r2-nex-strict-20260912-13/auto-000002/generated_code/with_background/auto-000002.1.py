import numpy as np



def upwind_flux_difference(q: np.ndarray, velocity: float, dx: float) -> np.ndarray:
    """Return the periodic first-order upwind derivative -d(a q)/dx for constant velocity."""

    if velocity >= 0:
        flux = velocity * q
    else:
        flux = velocity * np.roll(q, -1, axis=0)

    derivative = -(flux - np.roll(flux, 1, axis=0)) / dx
    return derivative
