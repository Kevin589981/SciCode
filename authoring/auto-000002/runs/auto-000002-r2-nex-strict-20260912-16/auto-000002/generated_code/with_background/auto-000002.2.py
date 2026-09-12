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




def lax_wendroff_step(q: np.ndarray, velocity: float, dx: float, dt: float) -> np.ndarray:
    """Advance q one step for q_t + a q_x = 0 using periodic Lax-Wendroff."""
    c = velocity * dt / dx

    q_new = q + dt * upwind_flux_difference(q, velocity, dx)

    q_right = np.roll(q, -1, axis=0)
    q_left = np.roll(q, 1, axis=0)
    second_difference = q_right - 2.0 * q + q_left

    if c >= 0.0:
        anti_diffusion = -0.5 * c * (1.0 - c) * second_difference
    else:
        anti_diffusion = 0.5 * c * (1.0 + c) * second_difference

    return q_new + anti_diffusion
