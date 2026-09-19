"""scicode-factory: ScienceIDE-style automated task construction for SciCode.

Pipeline stages (mirroring ScienceInfra demo/envs/<env>/factory):
    reference.py  -- witness/empty anchors + reference measurement per gold step
    operators.py  -- AST-based defect-injection candidates  ("inject")
    excise.py     -- function-body excision candidates       ("excise")
    funnel.py     -- execution-based validity filtering      (state machine)
    package.py    -- survivors -> sparse task directories
    gate_pack.py  -- static + leakage gates on packaged tasks
    build_dataset.py -- tasks -> QA dataset rows (hints L1/L2/L3, splits)
"""
