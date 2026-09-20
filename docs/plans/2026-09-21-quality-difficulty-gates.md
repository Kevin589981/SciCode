# Scientific quality and measured-difficulty gates

Date: 2026-09-21

## Boundary being closed

Task wording, source complexity, response length, executable pass/fail, and a
single model's opinion are not defensible difficulty measurements. The factory
therefore separates five decisions:

1. Is the task structurally and semantically deep?
2. Is it scientifically valid, answerable, and grounded in its source?
3. Did a particular solver actually solve it?
4. Is a particular reasoning trace useful supervision even if not solved?
5. Has the automatic selection policy been calibrated by humans well enough to
   release a training mixture?

## Gates

### Source-grounded task verification

An independent verifier sees the private source and authored task. It scores
scientific validity, source grounding, answerability, constraint consistency,
and shortcut resistance. At least two distinct supporting source quotes must be
copied exactly; quote grounding is checked deterministically. Fatal issues or
low scores reject the task before solver rollout.

### Model-relative difficulty

A versioned panel names distinct solver model IDs and attempt counts. Sampling
identity includes the entire panel/solver recipe. A separate evaluator measures
solution correctness and completeness, not trace training value. Difficulty is
derived from observed solve rate with a Wilson interval:

- insufficient distinct models/trials: `uncalibrated`;
- solve rate >= 0.8: `too_easy`;
- solve rate >= 0.6: `easy`;
- solve rate >= 0.3: `medium`;
- nonzero solve rate below 0.3: `hard`;
- zero solve rate: `unresolved` because invalid/impossible and genuinely hard
  tasks cannot be distinguished from failures alone.

### Trace value and judge consensus

Outcome-independent grading remains responsible for reasoning supervision and
per-message masks. Multiple judge model IDs can grade each trace. Candidate SFT
uses the primary judge's masks; release requires a configurable consensus of
solver-independent judges.

### Human calibration

The audit sampler deterministically covers archetype, difficulty, and automatic
accept/reject strata. The review packet is blind: model identity, outcomes, and
automatic judgments are placed in a separate answer key. Reviewers label task
validity, scientific depth, trace training value, critical errors, and trace
approval. Calibration rejoins the key, is tied to the exact population hash,
and checks minimum items/reviewers/overlap, task-validity
rate, mean depth/value, selection precision/recall, critical-error rate, and
pair agreement.

### Fail-closed release

The ordinary pipeline output is explicitly a candidate dataset. Release refuses
to proceed when calibration is missing, failed, or belongs to another trace
population. Default row gates additionally require calibrated `medium`/`hard`
difficulty, independent source verification, two independent judges, and 0.67
trainable consensus. Rejection reasons are emitted as an audit JSONL.

## Honest residual limits

No code can manufacture independent scientific evidence from one model. With
only Kimi-K3 available, plumbing and source-verification smoke tests can run,
but panel difficulty remains `uncalibrated`, multi-judge release remains
blocked, and human calibration remains pending until real reviewers label the
packet. These states are represented in artifacts rather than hidden.
