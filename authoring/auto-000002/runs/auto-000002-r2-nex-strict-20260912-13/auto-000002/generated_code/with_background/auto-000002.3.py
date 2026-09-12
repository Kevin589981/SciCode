import numpy as np

def upwind_flux_difference(q: np.ndarray, velocity: float, dx: float) -> np.ndarray:
    """Return the periodic first-order upwind derivative -d(a q)/dx for constant velocity."""

    if velocity >= 0:
        flux = velocity * q
    else:
        flux = velocity * np.roll(q, -1, axis=0)

    derivative = -(flux - np.roll(flux, 1, axis=0)) / dx
    return derivative


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



def integrate_periodic_advection(q0: np.ndarray, x: np.ndarray, velocity: float, t_final: float, steps: int) -> tuple[np.ndarray, float, float, float]:
    """Integrate periodic advection and return final state plus error and CFL diagnostics."""

    if steps <= 0:
        raise ValueError("steps must be positive")

    dx = x[1] - x[0]
    dt = t_final / steps
    L = x[-1] - x[0] + dx

    q_final = q0.copy()

    for _ in range(steps):
        q_final = lax_wendroff_step(q_final, velocity, dx, dt)

    shifted_x = np.mod(x - velocity * t_final - x[0], L)
    positions = shifted_x / dx

    left_index = np.mod(np.floor(positions).astype(int), len(q0))
    frac = positions - left_index
    right_index = np.mod(left_index + 1, len(q0))

    q_exact = (1.0 - frac) * q0[left_index] + frac * q0[right_index]

    diff = q_final - q_exact
    l1_error = dx * np.mean(np.abs(diff))
    max_error = np.max(np.abs(diff))
    cfl = abs(velocity) * dt / dx

    return q_final, l1_error, max_error, cfl
