---
name: scicode-task-authoring
description: Create lightweight scientific function-pair candidates for strict SciCode trace collection.
---

# Function-Pair Authoring

Follow `AGENTS.md` exactly.

1. Select one allowed scientific repository and inspect several meaningful functions in the same research session.
2. Create one independent SciCode problem with one subproblem per selected function. Add concise scientific background and keep the solver context identical to strict SciCode.
3. Write provenance, a private reference, candidate-owned tests, HDF5 targets, a redacted solver payload, and prompt snapshots.
4. Run the lightweight deterministic checks.
5. Invoke `scicode-avacore-run` once and stop the authoring session.
6. Let the controller export complete `(subproblem, trace)` rows directly.

Do not create a review child or run an extra authoring review turn. Do not call the model endpoint directly. Keep incomplete traces out of delivery, but keep complete incorrect solver answers as training traces.
