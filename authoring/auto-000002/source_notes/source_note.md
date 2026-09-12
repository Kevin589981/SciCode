# PyClaw periodic advection source note

Source repository: https://github.com/clawpack/pyclaw
Pinned commit: f522337ef75abef1153e2025a204b7ef4f7c5c9f
License: BSD-3-Clause
Paper DOI: 10.1137/110829270

Retrieved public files:
- examples/advection_1d/advection_1d.py
- src/pyclaw/classic/solver.py

Scientific fragment used: the advection example states the equation q_t + u q_x = 0, uses periodic boundary conditions, and the classic solver documentation describes a second-order Lax-Wendroff/LeVeque style update. The candidate implements the same periodic linear-advection workflow with NumPy only.
