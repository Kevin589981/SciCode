import numpy as np

def upwind_flux_difference(q: np.ndarray, velocity: float, dx: float) -> np.ndarray:
    """Return the periodic first-order upwind derivative -d(a q)/dx for constant velocity."""
    flux = velocity * q

    if velocity >= 0:
        flux_right = flux
        flux_left = np.roll(flux, 1, axis=0)
    else:
        flux_right = np.roll(flux, -1, axis=0)
        flux_left = flux

    derivative = -(flux_right - flux_left) / dx
    return derivative
