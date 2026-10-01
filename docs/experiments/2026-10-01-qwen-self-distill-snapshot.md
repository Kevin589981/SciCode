# Qwen self-distillation snapshot: 2026-10-01

## Identity and scope

- Repository: existing `Kevin589981/SciCode` fork (public); no new repository.
- Branch: `exp/2026-10-01-qwen35-self-distill-reuse-kimi-prompts`.
- Base commit: `9fd494f7f393ea2f665b61622dcecf37a84ee9cc`.
- Source workspace: `SciCode-v3`, including its previously uncommitted native
  Qwen rollout/export changes and the two existing factory documentation files.
- Local snapshot: `D:/1/desktop/scienceIDE/SciCode-qwen-self-distill-20261001`.
- Server snapshot: `/root/ScienceIDE-workspace/SciCode-qwen-self-distill-20261001`.

This freezes the existing Qwen experiment, not a new authoring pipeline or a
DeepSeek implementation. Existing workspaces, datasets and running jobs must not
be overwritten. Credentials, generated datasets and runtime state are not
published. The DeepSeek experiment is to branch from this snapshot separately.

## What this workflow actually does

1. `factory.reasoning.prepare_reused_tasks` joins an old SFT file to the cleaned
   trace index and original task files. It checks task hashes and that the old
   student-visible prompt contains the original question.
2. It deterministically selects distinct task hashes with a per-repository cap,
   exporting `tasks.jsonl`, `prompts.jsonl`, `source_index.jsonl` and a selection
   report. It copies the old system/user messages, never the old assistant.
3. `run_1k_qwen_native.sh` generates a fresh response using the Qwen solver,
   preserving native reasoning and final-answer channels through the streaming
   client. The model is configurable through environment variables.
4. `build_native_sft` exports complete responses as an assistant message
   containing both `<think>...</think>` and the final answer. It checks prompt
   provenance, task hashes, termination and reasoning boundaries. It does not
   certify scientific correctness; metadata explicitly records that limitation.

The launcher assumes prepared inputs exist relative to its own worktree. A clean
Git checkout does not include the server's generated data. For a new experiment,
prepare inputs into a new output directory, or explicitly configure a launcher
to use the original data paths. Do not copy private service keys into scripts.

## Why the existing subset has 1,000 tasks

The existing server selection report at
`/root/ScienceIDE-workspace/SciCode-v3/data-reasoning-qwen35-native-1k-v1/selection_report.json`
was inspected on 2026-10-01:

```json
{
  "target": 1000,
  "selected": 1000,
  "distinct_repositories": 831,
  "max_tasks_per_repository": 3,
  "audited_rows": 4939,
  "eligible_candidates": 4939,
  "rejected": {},
  "old_assistant_responses_copied": 0
}
```

This is an explicit pilot selection, not evidence that only 1,000 original rows
exist. It also identifies a different input population: 4,939 broader audit SFT
rows, rather than the later 4,586 supported reasoning-plus-answer rows. The
selection utility trusts its input population; it does not itself filter audit
dispositions. Passing a narrower input file matters.

The actual selected IDs were also joined to both exported datasets:

- All 1,000 belong to the 4,939-row export: 927 `model_supported_answer`,
  45 `reasoning_candidate`, and 28 `audit_error`.
- Only 927 belong to the later 4,586-row supported reasoning-plus-answer export.
- Both source exports have as many distinct task hashes as rows (4,939 and
  4,586 respectively), so task-hash deduplication alone is not the 1k limit.
- The existing Qwen run has 1,000 distinct traces: 991 finish with `stop`,
  nine with `length` and truncation. Its error sidecar contains 336 historical
  error events; these are not 336 currently missing tasks.

These observations describe the existing pilot only, not the outcome of a new
scientific audit or the count of structurally exportable SFT rows.

For the forthcoming teacher comparison, the intended population is:

`/root/ScienceIDE-workspace/SciCode-v3/datasets/scicode-v1-supported-cot-answer-sft-4586-v1/sft.jsonl`

Use the original index/task files for provenance. A requested target of 4,586
still requires checking distinct task hashes and the repository cap; the utility
raises an error instead of silently returning a smaller selection.

## Frozen behavior and known limitations

- The launcher defaults to Qwen3.5-35B-A3B, temperature 0.7, 196,608 maximum
  output tokens, 2,400-second timeout and concurrency 500. These are historical
  Qwen settings, not recommended DeepSeek service settings.
- The native exporter uses the Qwen-specific policy name
  `scicode-qwen-native-sft-v2` and repairs one observed duplicate-final boundary
  pattern. A new teacher needs explicitly reviewed channel/boundary handling.
- Rollout is a bounded thread-pool workflow, not the dynamic concurrency governor
  of the larger authoring batch pipeline. A new service needs its own limits.
- Resume identity includes model and run variant. Use separate output paths and
  a new variant for DeepSeek; do not resume into the Qwen traces.
- Native SFT construction is a structural completeness check, not a scientific
  audit, reasoning-efficiency assessment, or guarantee of improved student SFT.
- Matched-token comparisons must use the student Qwen tokenizer and training
  template/loss-mask policy, not different teachers' API token counts.

No model calls or training are started by recording this snapshot.
