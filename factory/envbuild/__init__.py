"""Environment construction layer (ports ScienceIDE environments/ workflow).

Upstream reference (ScienceIDE/environments/<env>/, NOT ScienceInfra):
    vet.py       codebase report: pin/archive/sha256/license + hazards scan
                 + LLM-proposed module decomposition -> validation/module.json
                 with an EXPERT APPROVAL checkpoint (upstream leaves human_ref
                 in the artifact; we keep it as a pending gate, not a formality)
    calibrate.py check calibration, the part whose toolchain upstream never
                 published (only measured evidence remains in their rubrics):
                 nominal+variant ulp-perturbation spread, fault-injection
                 probes, tolerance recommendation, double-run determinism,
                 warrant draft -> validation/<check>/{rubric.json,row.json}
    harvest.py   aggregate calibrated checks -> authoring/harvest.json +
                 WRITER-BRIEF.md for downstream task writers

Layout produced (mirrors ScienceIDE/environments/<env>/):
    environments/<env>/source/source.json          (pin/license/archive)
    environments/<env>/validation/module.json      (approval: pending)
    environments/<env>/validation/<check>/rubric.json  (warrant: pending)
    environments/<env>/validation/<check>/row.json
    environments/<env>/authoring/harvest.json
    environments/<env>/authoring/WRITER-BRIEF.md
"""
