# SciCode through AvaCore

This adapter keeps SciCode's official sequential prompts and HDF5 evaluator,
while using AvaCore's `OpenAIClient`, `RolloutEngine`, and immutable traces.
Each subproblem is stored as one `ChatTrace` subtrace. The output JSONL file
contains the complete trace tree and the official SciCode reward metadata.

From the AvaCore virtual environment on the development machine:

```bash
export PATH=/root/.local/bin:$PATH
export http_proxy=http://httpproxy-headless.kubebrain.svc.lg.shzhisuan.local:3128
export HTTP_PROXY=$http_proxy
export https_proxy=$http_proxy
export HTTPS_PROXY=$http_proxy
export no_proxy=localhost,127.0.0.0/8,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,10.250.0.0/16,.cn,kubernetes.docker.internal,*.cluster.local,*.svc,.svc,.cluster.local,.shzhisuan.local,.xuanyuan.run
export NO_PROXY=$no_proxy

cd /root/scicode-avacore/SciCode/eval/avacore
/root/scicode-avacore/AvaCore/.venv/bin/python scicode_avacore.py \
  --base-url http://117.135.59.14:5050 \
  --model Kimi-K3 \
  --h5py-file /root/scicode-avacore/data/test_data.h5 \
  --output /root/scicode-avacore/runs/kimi-k3 \
  --concurrency 1
```

Use `--problem-id 2` for the one-step smoke case. `--max-steps` is only for
prompt/trace debugging and intentionally does not claim an official score,
because the upstream evaluator requires all subproblem files for a problem.

The resulting `rollouts.jsonl` records `problem_correctness` in `reward.score`
and the official aggregate fields `total_correct` and `total_steps` in
`reward.metadata`. AvaCore's PostgreSQL store can be added around the same
`RolloutEngine` call when a database DSN is available; JSONL mode is useful for
local reproduction and trace inspection without changing the benchmark logic.

Per-problem working files use the same background-mode directory expected by
the official evaluator:

```text
<output>/<problem_id>/
  prompts/<with_background|without_background>/
  generated_code/<with_background|without_background>/<step_id>.py
  logs/evaluation_logs/<with_background|without_background>/<step_id>.log
```
