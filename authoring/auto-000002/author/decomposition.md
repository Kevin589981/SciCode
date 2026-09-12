# Decomposition

Problem ID: `auto-000002`

## Step 1: sign-aware periodic upwind flux-difference operator

The solver implements `-d(a q)/dx` on a uniform periodic grid using the first-order upwind numerical flux. For `a >= 0`, the upstream side of each interface is the left cell; for `a < 0`, it is the right cell. The tests cover positive and negative velocities and verify conservation of the cell average. This step isolates boundary-wrap semantics and the sign convention from time stepping.

## Step 2: Lax-Wendroff update reusing step 1

The solver computes the first-order derivative by calling `upwind_flux_difference(q, velocity, dx)`, then applies the sign-aware anti-diffusive correction that converts the first-order upwind update into the second-order Lax-Wendroff update. The tests check arithmetic against a small hand-computable stencil, shape/finite-value behavior for a negative-velocity sine wave, and mean conservation.

## Step 3: full integration and diagnostics

The solver repeatedly calls `lax_wendroff_step`, then compares the final state with the exact periodic translation using linear interpolation on the same cell centers. The returned diagnostics are `q_final`, `l1_error`, `max_error`, and `cfl`. The tests use smooth periodic initial data and compare against oracle values generated from the private reference implementation.

## Wrong implementation rationale

The included wrong implementation intentionally uses the fixed left-neighbor flux for all velocities and applies the positive-velocity anti-diffusion correction for all signs. That wrong path is scientifically plausible because it still advances advection, but it is not an upwind flux for negative velocity and breaks the intended periodic finite-volume semantics.
