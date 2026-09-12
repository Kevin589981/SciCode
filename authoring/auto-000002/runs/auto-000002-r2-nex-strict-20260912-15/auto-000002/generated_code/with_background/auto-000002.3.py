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



def integrate_periodic_advection(q0: np.ndarray, x: np.ndarray, velocity: float, t_final: float, steps: int) -> tuple[np.ndarray, float, float, float]:
    """Integrate periodic advection and return final state plus error and CFL diagnostics."""
    if steps <= 0:
        raise ValueError("steps must be positive")

    dx = x[1] - x[0]
    dt = t_final / steps
    L = x[-1] - x[0] + dx
    cfl = abs(velocity) * dt / dx

    q_final = np.array(q0, copy=True)
    for _ in range(steps):
        q_final = lax_wendroff_step(q_final, velocity, dx, dt)

    shifted = np.mod(x - velocity * t_final - x[0], L)
    frac = shifted / dx
    left_index = np.floor(frac).astype(int)
    right_index = (left_index + 1) % len(x)
    theta = frac - left_index

    q_exact = (1.0 - theta) * q0[..., left_index] + theta * q0[..., right_index]

    diff = np.abs(q_final - q_exact)
    l1_error = dx * np.mean(diff)
    max_error = np.max(diff)

    return q_final, l1_error, max_error, cfl
