# Repository Skills

Install or expose the skill directories under `.agents/skills/` to Kimi Code.
Use the skills as command wrappers so the controller does not need to reason
about AvaCore database schemas, trace serialization, or concurrent Git state.

| Skill | Owner | Purpose |
| --- | --- | --- |
| `scicode-worktree` | Kimi Code | Allocate and release one fixed worktree per candidate |
| `scicode-task-authoring` | Kimi Code | Create, validate, review, and revise a strict SciCode candidate |
| `scicode-avacore-run` | AvaCore boundary | Run Kimi strictly and return trace/token/evaluator artifacts |
| `scicode-delivery` | Delivery controller | Convert subproblem traces to SFT records and merge candidates |

Do not let a candidate session invent a different directory layout, output
schema, lock protocol, or merge command. Invoke the named skill and preserve
its manifest.
