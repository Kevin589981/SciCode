"""Solver runs + raw trace collection.

The PRIMARY deliverable of this pipeline is the raw trace (verbatim message
log + verifier feedback + rewards), from which QA pairs can be reconstructed
and which can be SFT'd directly. Extraction is downstream, not upstream.
"""
