numpy

def upwind_flux_difference(q: np.ndarray, velocity: float, dx: float) -> np.ndarray:
    """Return the periodic first-order upwind derivative -d(a q)/dx for constant velocity."""
    left = np.roll(q, 1)
    derivative = -velocity * (q - left) / dx
    return derivative

