import numpy as np



def upwind_flux_difference(q: np.ndarray, velocity: float, dx: float) -> np.ndarray:
    """Return the periodic first-order upwind derivative -d(a q)/dx for constant velocity."""
    if velocity >= 0:
        derivative = -velocity * (q - np.roll(q, 1, axis=-1)) / dx
    else:
        derivative = -velocity * (np.roll(q, -1, axis=-1) - q) / dx

    return derivative
