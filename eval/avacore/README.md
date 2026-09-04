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
