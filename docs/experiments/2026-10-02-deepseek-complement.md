# DeepSeek: remaining Kimi prompt population, 2026-10-02

## Authorization and purpose

The user asked to use the old ~9k prompt universe minus the currently reused
~4k population and authorized launching independently while away. This means
reuse the old questions and generate **new DeepSeek reasoning and code/answers**;
it does not mean reuse rejected Kimi answers or merely rerun their old audits.
Keep current and historical batches intact. No training, remote database writes,
new repository downloads, or unrelated model workspaces are involved.

## Verified population

The cleaned candidate universe has 9,440 unique old trace IDs. The current
supported-CoT-and-answer input population is an exact 4,586-row subset.
The complement has **4,854** prompts, without structural rejects or duplicate
visible questions. Overlap is zero by old ID, task ID, task hash, full public
prompt hash, and user-message hash.

Original answer audit statuses of these remaining prompts:

| Old Kimi answer disposition | Count |
| --- | ---: |
| quarantine | 4,501 |
| reasoning_candidate | 203 |
| audit_error | 149 |
| model_supported_answer, omitted from the old 4,586 for missing CoT | 1 |

These are answer judgments, not a certificate that every question is flawed.
New DeepSeek answers are independently audited by Kimi. The new population has
4,724 analysis-and-code and 130 analysis-and-experiment tasks, from 2,210
repositories (maximum 10 tasks per repository). No pure-analysis task is present.

Source artifacts (all read-only):

```text
/root/ScienceIDE-workspace/SciCode-v3/data-reasoning-10k-v1-cleaned-v1/candidate-cleaned.jsonl
/root/ScienceIDE-workspace/SciCode-v3/data-reasoning-10k-v1-cleaned-v1/index.jsonl
/root/ScienceIDE-workspace/SciCode-v3/data-reasoning-10k-v1-cleaned-v1/scientific-audit-v2.jsonl
/root/ScienceIDE-workspace/SciCode-deepseek-recovery-20261001/data-reasoning-deepseek-supported4586-recovery-v2/inputs
```

Only exact system/user messages are copied for generation. Private author
metadata and old assistant answers are not included in teacher requests.
`source_index.jsonl` stores prior audit disposition as provenance, not a new
teacher label or admission decision. `exclusions.jsonl` snapshots the protected
baseline; its SHA256 and all prepared artifact hashes are validated before calls.
No field falsely marks this population as previously supported answers.

## Code and output isolation

- Branch: `exp/2026-10-02-deepseek-reuse-remaining-kimi-prompts`.
- Local tree: `D:/1/desktop/scienceIDE/SciCode-deepseek-complement-20261002`.
- Remote tree: `/root/ScienceIDE-workspace/SciCode-deepseek-complement-20261002`.
- Data root: `data-reasoning-deepseek-complement4854-20261002-v1` inside that tree.
- Python: existing UV venv `/root/scicode-factory-venv/bin/python`.

The baseline 4,586 process continues unchanged in its own tree/output directory.
The new tree uses the same resilient generation/live-review pipeline: 500 teacher
workers and 500 reviewer workers, with review starting immediately per complete
trace, bounded queues containing disk offsets, a single review JSONL writer,
exclusive controller locks, cached review stages, and bounded recovery rounds.
No SQLite shared-write queue is introduced.

Model/API configuration remains the approved DeepSeek-V4-Flash-0731 teacher and
Kimi-K3 reviewer at the user's updated gateway. Context budgets: 262,144 each;
teacher output maximum 196,608; reviewer requested maximum 65,536, reduced when
necessary to preserve complete input within the context budget. Thinking is
explicitly enabled. Secrets exist only in the runtime environment, never Git,
configuration files, manifests, or printed commands.

The scientific review and reasoning review policies remain unchanged for a
controlled expansion. Both channels are retained even in quarantined records.
Final SFT is only the admitted subset; 4,854 prompts is not a promise of 4,854
usable training rows. The known consistency-review/probe authority limitations
are recorded in PROJECT_STATE.md and are not silently fixed mid-experiment.

## Prepare and validate (no model calls)

```bash
cd /root/ScienceIDE-workspace/SciCode-deepseek-complement-20261002
/root/scicode-factory-venv/bin/python -m factory.reasoning.prepare_complement \
  --candidates /root/ScienceIDE-workspace/SciCode-v3/data-reasoning-10k-v1-cleaned-v1/candidate-cleaned.jsonl \
  --index /root/ScienceIDE-workspace/SciCode-v3/data-reasoning-10k-v1-cleaned-v1/index.jsonl \
  --source-root /root/ScienceIDE-workspace/SciCode \
  --exclude-inputs /root/ScienceIDE-workspace/SciCode-deepseek-recovery-20261001/data-reasoning-deepseek-supported4586-recovery-v2/inputs \
  --prior-audit /root/ScienceIDE-workspace/SciCode-v3/data-reasoning-10k-v1-cleaned-v1/scientific-audit-v2.jsonl \
  --out-dir data-reasoning-deepseek-complement4854-20261002-v1/inputs
bash factory/reasoning/run_complement_deepseek_reuse.sh validate
```

Do not repeat prepare into nonempty inputs: it refuses overwrite. To resume an
existing run, retain the recorded factory commit, inputs, configuration, and run
identity; rerun pipeline only after the previous controller has exited.

## Launch / runtime artifacts

With generation and reviewer credentials supplied externally:

```bash
bash factory/reasoning/run_complement_deepseek_reuse.sh pipeline
```

This run is launched detached from SSH. `launch_manifest.json` records the PID,
timestamp, code commit, concurrency, and paths (no secrets). `launch.log` contains
controller round/export summaries. Runtime counters are in
`solver/progress.json` and `pipeline_progress.json`; incremental raw traces are
in `solver/traces.jsonl`, science reviews in `scientific-audit.jsonl`, and full-CoT
grades in `reasoning-quality.jsonl`. Provider outages use the existing circuit
breaker; exhausted or fatal errors remain visible, never counted as good data.

Final `native-vN/sft.jsonl` contains complete native CoT plus final answers,
including code. `reviewed-vN/` preserves supported, quarantine, reasoning
candidate, and error partitions with complete answers. `reviewed-vN/sft.jsonl`
is the current strict SFT-ready subset. Read `pipeline_report.json` for the
latest exported path; versioned exports do not overwrite prior snapshots.

## Verification before launch

Local: 171 factory tests passed. Remote: 171 tests plus 12 subtests passed.
Prepared selection: 4,854, structural rejects 0, duplicates/overlap 0,
old assistant responses copied 0. Maximum conservative prompt token estimate
is 11,405, within the approved teacher input/output context budget.
