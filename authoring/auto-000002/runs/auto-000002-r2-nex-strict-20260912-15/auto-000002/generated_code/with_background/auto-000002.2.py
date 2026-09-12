import numpy as np

def upwind_flux_difference(q: np.ndarray, velocity: float, dx: float) -> np.ndarray:
    """Return the periodic first-order upwind derivative -d(a q)/dx for constant velocity."""
    if velocity >= 0:
        derivative = -velocity * (q - np.roll(q, 1, axis=-1)) / dx
    else:
        derivative = -velocity * (np.roll(q, -1, axis=-1) - q) / dx

    return derivative



def lax_wendroff_step(q: np.ndarray, velocity: float, dx: float, dt: float) -> np.ndarray:
    """Advance q one step for q_t + a q_x = 0 using periodic Lax-Wendroff."""
    c = velocity * dt / dx

    # First-order sign-aware upwind update
    q_new = q + dt * upwind_flux_difference(q, velocity, dx)

    # Periodic centered second derivative
    q_ip1 = np.roll(q, -1, axis=-1)
    q_im1 = np.roll(q, 1, axis=-1)
    second_derivative = q_ip1 - 2.0 * q + q_im1

    # Sign-aware anti-diffusive correction
    if c >= 0:
        q_new = q_new + 0.5 * c * (c - 1.0) * second_derivative
    else:
        q_new = q_new + 0.5 * c * (c + 1.0) * second_derivative

    return q_new
