numpy

def upwind_flux_difference(q: np.ndarray, velocity: float, dx: float) -> np.ndarray:
    """Return the periodic first-order upwind derivative -d(a q)/dx for constant velocity."""
    left = np.roll(q, 1)
    derivative = -velocity * (q - left) / dx
    return derivative



def lax_wendroff_step(q: np.ndarray, velocity: float, dx: float, dt: float) -> np.ndarray:
    """Advance q one step for q_t + a q_x = 0 using periodic Lax-Wendroff."""
    c = velocity * dt / dx
    left = np.roll(q, 1)
    right = np.roll(q, -1)

    q_new = (
        q
        - 0.5 * c * (right - left)
        + 0.5 * c * c * (right - 2.0 * q + left)
    )

    return q_new




def integrate_periodic_advection(q0: np.ndarray, x: np.ndarray, velocity: float, t_final: float, steps: int) -> tuple[np.ndarray, float, float, float]:
    """Integrate periodic advection and return final state plus error and CFL diagnostics."""
    dx = x[1] - x[0]
    dt = t_final / steps
    L = x[-1] - x[0] + dx

    q_final = q0.copy()

    for _ in range(steps):
        q_final = lax_wendroff_step(q_final, velocity, dx, dt)

    shifted = np.mod(x - velocity * t_final - x[0], L)
    ratio = shifted / dx

    index = np.floor(ratio).astype(np.intp)
    frac = ratio - index

    q0_values = np.asarray(q0)
    q_exact = (1.0 - frac) * q0_values[index] + frac * q0_values[(index + 1) % q0_values.size]

    diff = q_final - q_exact
    l1_error = dx * np.mean(np.abs(diff))
    max_error = np.max(np.abs(diff))
    cfl = abs(velocity) * dt / dx

    return q_final, l1_error, max_error, cfl
