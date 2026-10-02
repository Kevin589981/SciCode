# Grounded scientific-code re-audit and repair

## Scope and policy

Preserve the recovery-4586 and complement-4854 runs, and write ONLY into the new
worktree/output. Audit v3 does not mutate legacy v2 or its exported datasets.
Inventory is SHA-bound to legacy candidates, strict SFT exports and new inputs.
The output contains fresh review records, original dispositions, raw responses,
native CoT/final answers, per-item caches, terminal partitions and versioned SFT.

Three Kimi roles run sequentially for each answer: source-anchored requirements,
science/code review, then full-context adjudication. The final role sees the same
full source and answer; no model-generated checklist is treated as authority.
Critical issues require an original-domain case with expected/delivered results.
Missing explanation/deliverables are repairable; narrative order is advisory.
Unknown essential facts remain unresolved, never treated as false by assumption.
Quote format repair accepts ONLY unique whitespace/source-role differences; it
stores both reported and literal source evidence. Words, punctuation, mathematical
operators and paraphrases are never fuzzily repaired. Real v1 smoke surfaced this
transport/format issue, so v2 lives in a separate remote worktree without stopping
v1 in-flight requests. The production branch has the `-v2` suffix.

Review only final answers for scientific/code correctness; productive-CoT grading
also reads native reasoning. Old valid grades are reused only when bound to the
same candidate hash. New traces are freshly graded. Both reasoning and content
must be trainable, not merely one channel. Complete Python assessment selects
implementation blocks; AST checks syntax and obvious unimplemented placeholders,
without executing arbitrary generated code or asserting universal correctness.

## Expected inventory

| Partition | Complete rows |
| --- | ---: |
| Preserve previously ready Python | 2,870 |
| Re-audit complete remaining candidates | 6,568 |
| Of selected: old scientific quarantine | 6,366 |
| Total complete native candidates | 9,438 |

Other selected rows include old reasoning candidates, an audit error, scientifically
supported rows lacking final training admission, and nine ready non-Python rows.
Two original truncated/no-final generations are not complete candidates and are
not in this inventory. Actual prepared `inputs/manifest.json` is authoritative.

## Automatic live repair

Supported original answers with productive CoT and complete Python are regained
without generation. Others get at most two DeepSeek revisions. A flawed task may
be explicitly rewritten with a change log and new task identity. The teacher sees
the complete original/revised question plus visible, fallible critique and code
requirements. New SFT retains that ENTIRE prompt, native thinking and final code.
It is not equivalent to changing only teacher weights while keeping exact prompts.
All revised traces pass v3 audit, code gate and productive-CoT/content grade before
admission. Never replace original rows, force acceptance or delete quarantines.

Terminal outcomes: `accepted_original`, `accepted_regenerated`, `needs_verification`,
`repair_exhausted`, `incomplete_generation`, `error`. The first two contribute new
SFT. Errors/incomplete generations can resume; uncertainty/exhaustion is retained
but not endlessly regenerated. Three controller passes retry transient incomplete
work without reissuing completed cached reviews or generations.

## Parallelism and artifacts

At most 500 reviewer requests and 500 teacher requests in this process. At most
1,000 outstanding item workers, independent provider breakers and 50 starts/sec
per provider, one JSONL writer, bounded heavy-response queue. Circuit-breaker
events use a nonblocking light queue to avoid writer/provider lock inversion.
Per-item atomic stage JSON allows recovery; a root lock prevents two controllers.
The runner does not use the old SQLite scheduler or hot-modify old running code.

Both total context budgets are 262,144. Teacher max completion 196,608; reviewer
requested max completion 131,072, reduced only to fit complete input, never by
truncating input. Task rewrite max completion 65,536. Gateway token estimates are
marked as estimates; actual reported input+output is checked when available.

Remote tree: `/root/ScienceIDE-workspace/SciCode-audit-repair-20261002-v2`.
Paths relative to this tree:

- `data-reasoning-grounded-audit-repair-20261002-v2/inputs/manifest.json`: selection.
- `.../progress.json`: current work and provider state.
- `.../audits.jsonl`, `grades.jsonl`, `generations.jsonl`, `outcomes.jsonl`: journals.
- `.../items/<hash>/`: cached reviews, full generations, terminal outcome and SFT.
- `.../release-vN/new-sft.jsonl`: newly regained/regenerated admitted rows.
- `.../release-vN/sft.jsonl`: those rows merged with preserved old Python examples.
- `.../release-vN/manifest.json`: export counts; versions never overwrite.

Deployment keys must exist only in process environment, never Git, request
metadata, manifests or normal logs. No training or database write is authorized.
