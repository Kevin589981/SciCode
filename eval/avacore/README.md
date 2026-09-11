# SciCode through AvaCore

This adapter keeps SciCode's official sequential prompts and HDF5 evaluator,
while using AvaCore's `OpenAIClient`, `RolloutEngine`, immutable traces, and
`PostgresBackend`. PostgreSQL is the primary record: each problem rollout,
subproblem trace, reward, status, and error is written there for AvaVisualizer.
The JSONL file is exported from that database after the run.

From the AvaCore virtual environment on the development machine:

```bash
export PATH=/root/.local/bin:$PATH
export http_proxy=http://httpproxy-headless.kubebrain.svc.lg.shzhisuan.local:3128
export HTTP_PROXY=$http_proxy
export https_proxy=$http_proxy
export HTTPS_PROXY=$http_proxy
export no_proxy=localhost,127.0.0.0/8,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,10.250.0.0/16,.cn,kubernetes.docker.internal,*.cluster.local,*.svc,.svc,.cluster.local,.shzhisuan.local,.xuanyuan.run
export NO_PROXY=$no_proxy
export POSTGRES=postgresql://user:password@127.0.0.1:5432/avacore

cd /root/scicode-avacore/SciCode/eval/avacore
/root/scicode-avacore/AvaCore/.venv/bin/python scicode_avacore.py \
  --base-url http://117.135.59.14:5050 \
  --model Kimi-K3 \
  --run-name scicode-kimi-smoke \
  --problem-id 2 \
  --problem-id 74 \
  --h5py-file /root/scicode-avacore/data/test_data.h5 \
  --output /root/scicode-avacore/runs/kimi-k3 \
  --concurrency 1
```

`BASE_URL`, `OPENAI_API_KEY`, `MODEL`, and `POSTGRES` are also read from the environment.
Both `https://host` and the usual OpenAI-style `https://host/v1` are accepted
as `BASE_URL`; the adapter normalizes the latter before AvaCore appends its API
path.

Use `--problem-id 2` for the one-step smoke case. `--max-steps` is only for
prompt/trace debugging and intentionally does not claim an official score,
because the upstream evaluator requires all subproblem files for a problem.

An authoring candidate can use the same runner without publishing it to the
Hugging Face dataset. Point `--problem-file` at the candidate's canonical
`public/problem.jsonl` and point `--h5py-file` at its private candidate-owned
oracle. The JSONL must retain the official SciCode fields (`problem_id`,
`required_dependencies`, `sub_steps`, and the test fields); evaluator-only
fields are read locally and are never sent as a separate prompt. For example:

```bash
cd /root/scicode-avacore/SciCode/eval/avacore
/root/scicode-avacore/AvaCore/.venv/bin/python scicode_avacore.py \
  --base-url http://117.135.59.14:5050 \
  --model Kimi-K3 \
  --run-name candidate-clean-003-agentic \
  --problem-file /root/scicode-avacore/SciCode/sandbox/candidate_task_clean_003/public/problem.jsonl \
  --h5py-file /root/scicode-avacore/SciCode/sandbox/candidate_task_clean_003/oracle/targets.h5 \
  --output /root/scicode-avacore/runs/candidate-clean-003-agentic \
  --score-by-subproblem \
  --concurrency 1
```

The candidate source is recorded in the AvaCore run configuration. The
PostgreSQL row and exported `rollouts.jsonl` therefore carry the same trace
contract as official runs, while the candidate's own HDF5 oracle remains the
private scoring artifact. `--problem-id` and `--limit` can still be used to
select records from a local candidate JSONL.

For a fully validated candidate, use `--candidate-dir` instead. The runner
resolves `public/problem.jsonl` and `oracle/targets.h5` from that directory,
checks the candidate manifest before contacting the provider, and writes a
promotability manifest beside the exported rollouts:

```bash
/root/scicode-avacore/AvaCore/.venv/bin/python scicode_avacore.py \
  --candidate-dir /root/scicode-authoring/candidates/CANDIDATE_ID \
  --base-url http://10.100.184.127:5050 \
  --model "$KIMI_MODEL" \
  --postgres "$POSTGRES" \
  --output /root/scicode-authoring/candidates/CANDIDATE_ID/runs/avacore-final \
  --export /root/scicode-authoring/candidates/CANDIDATE_ID/runs/avacore-final/rollouts.jsonl \
  --run-name CANDIDATE_ID-r1-final \
  --temperature 0.6 \
  --max-tokens 262144 \
  --timeout 7200 \
  --score-by-subproblem
```

The default output limit is 262,144 tokens. A run with `--max-steps` is marked
debug-only and cannot be promoted to the formal dataset.

The resulting database rollouts record `problem_correctness` in `reward.score`
and the official aggregate fields `total_correct` and `total_steps` in
`reward.metadata`. The generated `rollouts.jsonl` is produced by AvaCore's
database exporter and contains the same records AvaVisualizer reads. Use
`--export PATH` to choose another export destination.

Per-problem working files use the same background-mode directory expected by
the official evaluator:

```text
<output>/<problem_id>/
  prompts/<with_background|without_background>/
  generated_code/<with_background|without_background>/<step_id>.py
  logs/evaluation_logs/<with_background|without_background>/<step_id>.log
```
