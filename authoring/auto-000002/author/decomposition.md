# Decomposition

Problem ID: `auto-000002`

## Step 1: periodic flux-difference operator

The solver implements `-d(a q)/dx` on a uniform periodic grid. The step tests both positive and negative velocities and verifies conservation of the cell average. This step isolates boundary-wrap semantics from time stepping.

## Step 2: Lax-Wendroff update

The solver implements the second-order update
`q_new = q - 0.5*c*(q_right - q_left) + 0.5*c*c*(q_right - 2*q + q_left)`
with `c = a dt / dx`. The tests check the arithmetic against a small hand-computable stencil and verify mean conservation for smooth periodic data.

## Step 3: full integration and diagnostics

The solver repeatedly applies the Lax-Wendroff step, then compares with the exact periodic translation using linear interpolation on the same cell centers. The returned diagnostics are `q_final`, `l1_error`, `max_error`, and `cfl`. The tests use smooth periodic initial data and compare against oracle values generated from the private reference implementation.

## Wrong implementation rationale

The included wrong implementation intentionally uses zero-gradient boundaries in step 1 and first-order upwind Euler in step 2. That wrong path is scientifically plausible because it still advances advection, but it breaks the periodic finite-volume contract and fails the intended numerical/periodicity checks.
