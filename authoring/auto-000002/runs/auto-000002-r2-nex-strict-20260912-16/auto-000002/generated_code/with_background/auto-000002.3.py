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




def integrate_periodic_advection(q0: np.ndarray, x: np.ndarray, velocity: float, t_final: float, steps: int) -> tuple[np.ndarray, float, float, float]:
    """Integrate periodic advection and return final state plus error and CFL diagnostics."""
    dx = x[1] - x[0]
    dt = t_final / steps
    L = x[-1] - x[0] + dx

    q_final = q0.copy()
    for _ in range(steps):
        q_final = lax_wendroff_step(q_final, velocity, dx, dt)

    shifted = np.mod(x - velocity * t_final - x[0], L)
    frac = shifted / dx
    idx = np.floor(frac).astype(int)
    theta = frac - idx

    q_exact = (1.0 - theta) * q0[idx] + theta * q0[(idx + 1) % len(q0)]

    diff = q_final - q_exact
    l1_error = dx * np.mean(np.abs(diff))
    max_error = np.max(np.abs(diff))
    cfl = abs(velocity) * dt / dx

    return q_final, l1_error, max_error, cfl
