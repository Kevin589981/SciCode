"""Problem authoring from upstream scientific codebases.

ScienceIDE principle ported: "propose broadly; establish validity by
execution". An LLM proposes SciCode-style sub-steps over functions mined from
a pinned upstream repo; this package executes every proposal against the
original code and keeps only what passes the gates.

    mine.py     AST heuristics over a repo checkout -> candidate functions
    llm.py      OpenAI-compatible client (shared with factory.solve)
    propose.py  LLM turns a mined function into a sub-step proposal
    verify.py   execute proposals vs the original: witness/empty/determinism
                gates, target capture into seeds/test_data.h5, seed emission
    novelty.py  screen proposals against the official SciCode benchmark
"""
