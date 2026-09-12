import numpy as np

def lax_wendroff_step(q: np.ndarray, velocity: float, dx: float, dt: float) -> np.ndarray:
    """Advance q one step for q_t + a q_x = 0 using periodic Lax-Wendroff."""

    courant = velocity * dt / dx

    first_order_derivative = upwind_flux_difference(q, velocity, dx)
    q_new = q + dt * first_order_derivative

    q_left = np.roll(q, 1, axis=0)
    q_right = np.roll(q, -1, axis=0)
    second_derivative = q_right - 2.0 * q + q_left

    if courant >= 0.0:
        anti_diffusion = -0.5 * courant * (1.0 - courant) * second_derivative
    else:
        anti_diffusion = 0.5 * courant * (1.0 + courant) * second_derivative

    q_new = q_new + anti_diffusion
    return q_new

