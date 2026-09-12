# Revision r1 solver execution evidence

The strict AvaCore run `auto-000002-r1-nex-strict-20260912-11` completed the
three model calls and exported complete traces, but the official SciCode
evaluator returned zero correct subproblems.

The generated evaluator files begin with the value of
`required_dependencies` followed by the extracted code. The candidate set
that field to the bare string `numpy`, so every generated file begins with:

```text
numpy
```

Running the evaluator file with the AvaCore Python environment reproduces:

```text
NameError: name 'numpy' is not defined
```

This is a candidate-format failure, not a solver-quality result. Do not
release revision r1. Revision r2 must use an executable SciCode dependency
declaration (for example `import numpy as np`), regenerate the visible
contract, prompt snapshots, oracle/hash reports, and repeat child review and
strict AvaCore evaluation.
