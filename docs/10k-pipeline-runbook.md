# SciCode 10k Pipeline Runbook

This runbook operates the repository on yicloud. Kimi Code is the authoring
controller. Kimi is the solver model called only through AvaCore.

## Prepare yicloud

Connect with the configured SSH alias:

```bash
ssh yicloud
```

Create a fixed workspace and clone the fork once:

```bash
mkdir -p /root/scicode-authoring
cd /root/scicode-authoring
git clone https://github.com/Kevin589981/SciCode.git repository
cd repository
git fetch --all --prune
git switch codex/scicode-10k-pipeline
```

Set the proxy only while fetching GitHub, arXiv, Hugging Face, or Docker Hub
sources:

```bash
export http_proxy=http://httpproxy-headless.kubebrain.svc.lg.shzhisuan.local:3128
export https_proxy="$http_proxy"
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$http_proxy"
```

Do not put API keys in shell history, candidate files, or trace manifests. Keep
`POSTGRES`, `OPENAI_API_KEY`, and any provider-specific key in the yicloud
environment that launches AvaCore.

## Start Kimi Code authoring

Run Kimi Code from the repository checkout. Give it one candidate id and tell
it to read `AGENTS.md` and the four repository skills:

```bash
cd /root/scicode-authoring/repository
kimi -p "Read AGENTS.md and .agents/skills. Create candidate cand-000001 in a dedicated worktree. Follow every authoring, child-review, strict AvaCore handoff, and delivery command. Do not use official SciCode tasks or test_data.h5. Stop the authoring session after submitting the AvaCore handoff and record the resume session id."
```

The controller must use `scripts/worktree.py`, create
`authoring/<CANDIDATE_ID>` inside the returned worktree, run the candidate checks, and
ask a fresh child agent to review the public task before handoff.

## Submit and monitor the Kimi solver run

The AvaCore skill submits a detached process and writes
`candidate/runs/<run_id>/handoff.json`. Poll it with:

```bash
python scripts/avacore_run_status.py \
  /root/scicode-authoring/candidates/cand-000001/authoring/cand-000001/runs/cand-000001-r1/handoff.json
```

After the runner closes, require these files before resuming Kimi Code:

```text
runs/<run_id>/manifest.json
runs/<run_id>/rollouts.jsonl
runs/<run_id>/avacore.log
```

Resume the same Kimi Code session with the handoff and exported trace paths:

```bash
kimi -r SESSION_ID -p "Read runs/RUN_ID/handoff.json and runs/RUN_ID/manifest.json. Inspect the complete rollouts.jsonl per subproblem, classify trace and task-quality findings, and continue the revision or delivery procedure in AGENTS.md."
```

Read each completed subproblem without importing provider dependencies:

```bash
python3 scripts/inspect_trace.py \
  --candidate-dir "$CANDIDATE_DIR" \
  --rollouts "$CANDIDATE_DIR/runs/$RUN_ID/rollouts.jsonl" \
  --output "$CANDIDATE_DIR/validation/trace_details.json" \
  --max-text-chars 8000
```

## Deliver samples

After Kimi Code records a passing release decision, invoke the delivery skill
or run its deterministic wrapper:

```bash
python scripts/run_candidate_pipeline.py \
  --candidate-dir /root/scicode-authoring/candidates/cand-000001/authoring/cand-000001 \
  --delivery-root /root/scicode-authoring/delivery \
  --rollouts /root/scicode-authoring/candidates/cand-000001/authoring/cand-000001/runs/RUN_ID/rollouts.jsonl \
  --run-manifest /root/scicode-authoring/candidates/cand-000001/authoring/cand-000001/runs/RUN_ID/manifest.json \
  --target-count 10000
```

The delivery output is `delivery/dataset.jsonl`; each line is one
`(subproblem, trace)` sample. `delivery/registry.jsonl` prevents duplicate
trace insertion, and `delivery/summary.json` records counts and invalid runs.
The worktree branch remains in Git history after the copy is cleaned.

## Inspect failures

Check the handoff log, run manifest, and database run independently:

```bash
tail -n 100 /root/scicode-authoring/candidates/cand-000001/authoring/cand-000001/runs/RUN_ID/avacore.log
cat /root/scicode-authoring/candidates/cand-000001/authoring/cand-000001/runs/RUN_ID/manifest.json
python scripts/avacore_run_status.py /root/scicode-authoring/candidates/cand-000001/authoring/cand-000001/runs/RUN_ID/handoff.json
```

Classify provider/network/queue/database errors as infrastructure. Classify a
`finish_reason` of `length` at 262,144 tokens as a normal trace status. Retry
transient or incomplete runs up to three times, then let Kimi Code decide task
revision or rejection using the recorded evidence.
