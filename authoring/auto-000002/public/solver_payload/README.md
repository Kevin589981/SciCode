# auto-000002 candidate files

This directory contains the solver-visible public files for candidate `auto-000002`.

## Visible files

- `problem.jsonl`: canonical SciCode record for one problem with three ordered subproblems.
- `solver_payload/`: redacted source notes and provenance visible to the solver.
- `checks/`: public invariant checks that do not include oracle values.
- `prompt_snapshot/with_background/`: rendered strict background prompt snapshots for each ordered subproblem.

The candidate is based on the external PyClaw periodic advection example and classic second-order solver documentation. Private oracle, reference, independent, wrong implementation, and run artifacts are kept outside `public/`.
