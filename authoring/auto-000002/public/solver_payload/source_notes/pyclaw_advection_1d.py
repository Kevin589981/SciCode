#!/usr/bin/env python
# encoding: utf-8

r"""
One-dimensional advection
=========================

Solve the linear advection equation:

.. math:: 
    q_t + u q_x = 0.

Here q is the density of some conserved quantity and u is the velocity.

The initial condition is a Gaussian and the boundary conditions are periodic.
The final solution is identical to the initial data because the wave has
crossed the domain exactly once.
"""
import numpy as np
from clawpack import riemann

# Fragment retrieved from clawpack/pyclaw at commit
# f522337ef75abef1153e2025a204b7ef4f7c5c9f.
