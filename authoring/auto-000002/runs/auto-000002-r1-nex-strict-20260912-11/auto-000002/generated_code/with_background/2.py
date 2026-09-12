numpy


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

