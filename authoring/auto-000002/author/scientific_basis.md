# Scientific basis

The candidate is derived from the PyClaw 1-D advection example and classic solver documentation. PyClaw formulates the linear advection equation as `q_t + u q_x = 0`, applies periodic boundary conditions in the 1-D example, and documents a classic second-order Lax-Wendroff/LeVeque update in `src/pyclaw/classic/solver.py`.

The candidate asks for a minimal NumPy implementation of that scientific workflow:

1. A conservative periodic flux-difference operator that uses a sign-aware first-order upwind numerical flux. Positive velocity takes the left periodic neighbor at each interface; negative velocity takes the right periodic neighbor.
2. A second-order Lax-Wendroff update that reuses the sign-aware upwind derivative and applies the matching sign-aware anti-diffusive correction.
3. A full integration routine that compares the numerical periodic translation with the exact analytic translation and reports L1, max-norm, and CFL diagnostics.

This is a numerical-methods task with unit/shape conventions, periodic boundary semantics, CFL reporting, and a known wrong implementation path: using the fixed left-neighbor difference for negative velocity changes the scientific semantics and fails the oracle tests.
