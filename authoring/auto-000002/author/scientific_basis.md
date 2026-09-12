# Scientific basis

The candidate is derived from the PyClaw 1-D advection example and classic solver documentation. PyClaw formulates the linear advection equation as `q_t + u q_x = 0`, applies periodic boundary conditions in the 1-D example, and documents a classic second-order Lax-Wendroff/LeVeque update in `src/pyclaw/classic/solver.py`.

The candidate asks for a minimal NumPy implementation of that scientific workflow:

1. A conservative periodic flux-difference operator for constant advective flux `F = a q`.
2. A second-order Lax-Wendroff update using periodic left and right neighbors.
3. A full integration routine that compares the numerical periodic translation with the exact analytic translation and reports L1, max-norm, and CFL diagnostics.

This is a numerical-methods task with unit/shape conventions, periodic boundary semantics, CFL reporting, and a known wrong implementation path: using non-periodic boundaries or first-order Euler stepping changes the scientific semantics and fails the oracle tests.
