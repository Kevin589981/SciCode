"""Deterministic scientific problem blueprints for query-only synthesis.

The builders emit questions, not solutions.  They intentionally contain no
reference implementation or numeric oracle; those artifacts are created by a
later answer/evaluation stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import Callable

try:  # Works both as ``python scripts/...`` and as ``scripts....`` in tests.
    from .query_schema import validate_query
except ImportError:  # pragma: no cover - exercised by the script entry point.
    from query_schema import validate_query


def _num(value: float) -> str:
    return f"{value:.7g}"


def _fname(prefix: str, problem_id: str) -> str:
    return f"{prefix}_{problem_id.replace('-', '_')}"


def _step(
    problem_id: str,
    number: int,
    description: str,
    function_header: str,
    test_cases: list[str],
    return_line: str,
    background: str,
) -> dict[str, object]:
    return {
        "step_number": f"{problem_id}.{number}",
        "step_description_prompt": description,
        "function_header": function_header,
        "test_cases": test_cases,
        "return_line": return_line,
        "step_background": background,
    }


def _problem(
    problem_id: str,
    name: str,
    description: str,
    io: str,
    dependencies: str,
    steps: list[dict[str, object]],
    general_tests: list[str],
    background: str,
) -> dict[str, object]:
    value = {
        "problem_name": name,
        "problem_id": problem_id,
        "problem_description_main": description,
        "problem_io": io,
        "required_dependencies": dependencies,
        "sub_steps": steps,
        "general_tests": general_tests,
        "problem_background_main": background,
    }
    validate_query(value)
    return value


def _diffusion(problem_id: str, rng: Random) -> dict[str, object]:
    n = rng.choice([24, 32, 40, 48, 56])
    dx = rng.choice([0.025, 0.04, 0.05, 0.08])
    dy = rng.choice([0.03, 0.05, 0.07])
    kx = rng.uniform(0.04, 0.22)
    ky = rng.uniform(0.03, 0.18)
    dt = min(dx * dx / (4 * kx), dy * dy / (4 * ky)) * rng.uniform(0.25, 0.65)
    lap = _fname("anisotropic_laplacian", problem_id)
    stepper = _fname("diffusion_cn_step", problem_id)
    diag = _fname("diffusion_observables", problem_id)
    return _problem(
        problem_id,
        f"Anisotropic periodic diffusion with energy diagnostics [{problem_id}]",
        "Implement a two-dimensional periodic diffusion experiment with unequal "
        "principal diffusivities.  Construct the spatial operator, advance a "
        "localized scalar field with a Crank-Nicolson update, and report physical "
        "conservation and dissipation diagnostics.",
        f"The field is a float array of shape ({n}, {n}) on a periodic rectangle "
        f"with dx={_num(dx)} and dy={_num(dy)}. Diffusivities kx={_num(kx)} and "
        f"ky={_num(ky)} have area^2/time units; return arrays and Python floats "
        "with the ordering stated by each function.",
        "import numpy as np",
        [
            _step(
                problem_id,
                1,
                f"Build the conservative periodic anisotropic Laplacian for a field on a uniform grid. Use centered second differences in each direction and return kx*d2(field)/dx2 + ky*d2(field)/dy2 without modifying the input.",
                f"def {lap}(field: np.ndarray, dx: float, dy: float, kx: float, ky: float) -> np.ndarray:",
                [
                    f"field = np.arange({n*n}, dtype=float).reshape({n}, {n})\nout = {lap}(field, {_num(dx)}, {_num(dy)}, {_num(kx)}, {_num(ky)})\nassert out.shape == field.shape\nassert np.all(np.isfinite(out))",
                    f"field = np.sin(np.linspace(0.0, 2.0*np.pi, {n}, endpoint=False))[:, None] * np.cos(np.linspace(0.0, 2.0*np.pi, {n}, endpoint=False))[None, :]\nout = {lap}(field, {_num(dx)}, {_num(dy)}, {_num(kx)}, {_num(ky)})\nassert np.allclose(out.mean(), 0.0, atol=1e-12)",
                    f"field = np.zeros(({n}, {n})); field[{n//2}, {n//3}] = 1.0\nout = {lap}(field, {_num(dx)}, {_num(dy)}, {_num(kx)}, {_num(ky)})\nassert np.allclose(out, target)",
                ],
                "return laplacian",
                "Periodic finite differences wrap both axes.  The divergence form "
                "must conserve the spatial mean, while anisotropy changes the rate "
                "of smoothing along each coordinate direction.",
            ),
            _step(
                problem_id,
                2,
                f"Advance the diffusion equation by a requested number of Crank-Nicolson steps. Reuse {lap} as the spatial operator, solve the implicit linear system for each step, and return a new field.",
                f"def {stepper}(field: np.ndarray, dx: float, dy: float, kx: float, ky: float, dt: float, steps: int) -> np.ndarray:",
                [
                    f"field = np.zeros(({n}, {n})); field[{n//2}, {n//2}] = 1.0\nout = {stepper}(field, {_num(dx)}, {_num(dy)}, {_num(kx)}, {_num(ky)}, {_num(dt)}, 1)\nassert out.shape == field.shape\nassert np.all(np.isfinite(out))",
                    f"field = np.ones(({n}, {n}))\nout = {stepper}(field, {_num(dx)}, {_num(dy)}, {_num(kx)}, {_num(ky)}, {_num(dt)}, 4)\nassert np.allclose(out, field, atol=1e-10)",
                    f"rng = np.random.default_rng(7); field = rng.normal(size=({n}, {n}))\nout = {stepper}(field, {_num(dx)}, {_num(dy)}, {_num(kx)}, {_num(ky)}, {_num(dt)}, 3)\nassert np.allclose(out, target)",
                ],
                "return field_new",
                "Crank-Nicolson averages explicit and implicit diffusion operators. "
                "The update is unconditionally stable for this linear problem, but "
                "the discrete mean should remain invariant under periodic boundaries.",
            ),
            _step(
                problem_id,
                3,
                f"Run the diffusion experiment and return the final field, conserved mass, dissipated quadratic energy, and the dimensionless Fourier-mode decay estimate.",
                f"def {diag}(field0: np.ndarray, dx: float, dy: float, kx: float, ky: float, dt: float, steps: int) -> tuple[np.ndarray, float, float, float]:",
                [
                    f"field0 = np.zeros(({n}, {n})); field0[{n//2}, {n//2}] = 1.0\nout = {diag}(field0, {_num(dx)}, {_num(dy)}, {_num(kx)}, {_num(ky)}, {_num(dt)}, 2)\nassert out[0].shape == field0.shape\nassert np.allclose(np.asarray(out[1:]), np.asarray(target[1:]))",
                    f"field0 = np.ones(({n}, {n}))\nout = {diag}(field0, {_num(dx)}, {_num(dy)}, {_num(kx)}, {_num(ky)}, {_num(dt)}, 5)\nassert np.allclose(out, target)",
                    f"rng = np.random.default_rng(11); field0 = rng.normal(size=({n}, {n}))\nout = {diag}(field0, {_num(dx)}, {_num(dy)}, {_num(kx)}, {_num(ky)}, {_num(dt)}, 6)\nassert np.all(np.isfinite(np.asarray(out[1:])))",
                ],
                "return field_final, mass, energy, mode_decay",
                "For a diffusion equation, total mass is conserved while the L2-like "
                "energy decreases.  Reporting both invariants and a mode-scale "
                "quantity distinguishes a stable implementation from one that only "
                "produces a plausible-looking image.",
            ),
        ],
        [
            "assert np.isfinite(np.asarray(result)).all()",
            "assert abs(float(mass_final - mass_initial)) < tolerance",
            "assert energy_final <= energy_initial + tolerance",
        ],
        "A periodic anisotropic diffusion model is used in image restoration and "
        "continuum transport.  The numerical task couples a conservative stencil, "
        "an implicit time integrator, and physically meaningful diagnostics.",
    )


def _kepler(problem_id: str, rng: Random) -> dict[str, object]:
    e = rng.uniform(0.08, 0.82)
    mu = rng.uniform(0.6, 2.4)
    a = rng.uniform(1.2, 5.0)
    dt = rng.uniform(0.05, 0.6)
    solve = _fname("solve_kepler_anomaly", problem_id)
    state = _fname("orbital_state_from_elements", problem_id)
    prop = _fname("propagate_kepler_orbit", problem_id)
    return _problem(
        problem_id,
        f"Elliptic orbit propagation and invariant checks [{problem_id}]",
        "Implement a small astrodynamics pipeline for an elliptic two-body orbit. "
        "Solve Kepler's equation, convert orbital elements to a Cartesian state, "
        "and propagate the state while reporting conserved invariants.",
        f"Use gravitational parameter mu={_num(mu)} in consistent distance^3/time^2 "
        f"units, semimajor axis a={_num(a)}, eccentricity near {_num(e)}, and "
        "angles in radians. Return Cartesian vectors as float arrays.",
        "import numpy as np",
        [
            _step(
                problem_id,
                1,
                "Solve M = E - e*sin(E) for the eccentric anomaly E. Use a numerically stable Newton iteration with a bounded convergence condition and preserve the input angle modulo 2*pi.",
                f"def {solve}(mean_anomaly: float, eccentricity: float, tolerance: float = 1e-12, max_iterations: int = 80) -> float:",
                [
                    f"out = {solve}(0.0, {_num(e)})\nassert np.allclose(out, target)",
                    f"out = {solve}(2.0*np.pi + 0.37, {_num(e)})\nassert np.allclose(out, target)",
                    f"out = {solve}(-1.2, {_num(min(e*0.7, 0.7))})\nassert np.isfinite(out)",
                ],
                "return eccentric_anomaly",
                "Kepler's equation maps mean anomaly to eccentric anomaly.  Newton's "
                "method is fast for moderate eccentricity but needs a clear stopping "
                "criterion and angle normalization.",
            ),
            _step(
                problem_id,
                2,
                f"Convert classical elliptic elements to position and velocity in an inertial frame. Reuse {solve} for the eccentric anomaly and apply the specified rotation convention.",
                f"def {state}(semi_major_axis: float, eccentricity: float, inclination: float, raan: float, argument_of_periapsis: float, mean_anomaly: float, mu: float) -> tuple[np.ndarray, np.ndarray]:",
                [
                    f"out = {state}({_num(a)}, {_num(e)}, 0.2, 0.4, 0.7, 1.1, {_num(mu)})\nassert np.allclose(np.asarray(out), np.asarray(target))",
                    f"out = {state}({_num(a*1.3)}, {_num(e*0.5)}, 0.0, 0.0, 0.0, 0.0, {_num(mu)})\nassert out[0].shape == (3,) and out[1].shape == (3,)",
                    f"out = {state}({_num(a*0.8)}, {_num(min(e*1.1, 0.9))}, 1.0, -0.5, 2.2, -0.8, {_num(mu)})\nassert np.all(np.isfinite(np.asarray(out)))",
                ],
                "return position, velocity",
                "The perifocal state follows from the eccentric anomaly and vis-viva "
                "relations.  Three Euler-angle rotations map it to inertial axes; "
                "the order and handedness are part of the scientific convention.",
            ),
            _step(
                problem_id,
                3,
                f"Propagate an elliptic orbit by a time interval and return the new Cartesian state, specific angular momentum, and specific orbital energy.",
                f"def {prop}(position: np.ndarray, velocity: np.ndarray, dt: float, mu: float) -> tuple[np.ndarray, np.ndarray, float, float]:",
                [
                    f"r, v = {state}({_num(a)}, {_num(e)}, 0.3, 0.6, 0.2, 0.9, {_num(mu)})\nout = {prop}(r, v, {_num(dt)}, {_num(mu)})\nassert np.allclose(np.asarray(out), np.asarray(target))",
                    f"r = np.array([{_num(a)}, 0.0, 0.0]); v = np.array([0.0, np.sqrt({_num(mu)}/{_num(a)}), 0.0])\nout = {prop}(r, v, {_num(dt*2)}, {_num(mu)})\nassert np.all(np.isfinite(np.asarray(out)))",
                    f"r, v = {state}({_num(a*1.1)}, {_num(e*0.8)}, 0.8, 1.1, 0.3, -0.4, {_num(mu)})\nout = {prop}(r, v, {_num(dt*0.5)}, {_num(mu)})\nassert np.isfinite(out[2]) and np.isfinite(out[3])",
                ],
                "return position_new, velocity_new, angular_momentum, specific_energy",
                "In a Kepler two-body problem, angular momentum and specific energy "
                "are invariants.  Propagation can use a universal-variable or anomaly "
                "formulation, but must preserve the chosen Cartesian convention.",
            ),
        ],
        [
            "assert np.all(np.isfinite(position))",
            "assert np.linalg.norm(angular_momentum) > 0",
            "assert abs(energy_after - energy_before) < tolerance",
        ],
        "Orbit determination and mission design rely on repeated transformations "
        "between anomalies, elements, and Cartesian states.  The coupled steps test "
        "periodic angles, rotation order, and physical invariants.",
    )


def _reaction(problem_id: str, rng: Random) -> dict[str, object]:
    kf = rng.uniform(0.15, 1.8)
    kr = rng.uniform(0.04, 0.7)
    kd = rng.uniform(0.01, 0.25)
    dt = rng.uniform(0.002, 0.03)
    rhs = _fname("reaction_rhs", problem_id)
    rk = _fname("reaction_rk4_step", problem_id)
    integrate = _fname("integrate_reaction_network", problem_id)
    return _problem(
        problem_id,
        f"Reversible reaction network with conserved moiety [{problem_id}]",
        "Model a well-mixed reaction A+B <-> C with a first-order loss channel. "
        "Implement its mass-action vector field, a fourth-order Runge-Kutta step, "
        "and a trajectory routine that reports a conservation residual.",
        f"Concentrations are nonnegative molar quantities. Rate constants are kf={_num(kf)}, "
        f"kr={_num(kr)}, kd={_num(kd)} in reciprocal time units; integrate with dt={_num(dt)}.",
        "import numpy as np",
        [
            _step(
                problem_id,
                1,
                "Evaluate the mass-action derivative for concentrations [A, B, C]. The reversible flux is kf*A*B - kr*C and the loss channel removes C; return a length-three derivative without mutating concentrations.",
                f"def {rhs}(concentrations: np.ndarray, k_forward: float, k_reverse: float, k_loss: float) -> np.ndarray:",
                [
                    f"c = np.array([1.2, 0.8, 0.3]); out = {rhs}(c, {_num(kf)}, {_num(kr)}, {_num(kd)})\nassert np.allclose(out, target)",
                    f"c = np.zeros(3); out = {rhs}(c, {_num(kf)}, {_num(kr)}, {_num(kd)})\nassert np.allclose(out, 0.0)",
                    f"c = np.array([0.2, 1.5, 0.4]); out = {rhs}(c, {_num(kf*0.7)}, {_num(kr*1.2)}, {_num(kd*0.5)})\nassert out.shape == (3,)",
                ],
                "return derivative",
                "Mass-action kinetics couples reactant concentrations through a "
                "nonlinear flux.  Writing the stoichiometric signs explicitly is "
                "essential for the A+B to C conversion and the C loss channel.",
            ),
            _step(
                problem_id,
                2,
                f"Advance the reaction state by one classical RK4 step. Call {rhs} for all four stages and return a fresh concentration vector.",
                f"def {rk}(concentrations: np.ndarray, dt: float, k_forward: float, k_reverse: float, k_loss: float) -> np.ndarray:",
                [
                    f"c = np.array([1.0, 0.5, 0.1]); out = {rk}(c, {_num(dt)}, {_num(kf)}, {_num(kr)}, {_num(kd)})\nassert np.allclose(out, target)",
                    f"c = np.array([0.0, 0.0, 0.0]); out = {rk}(c, {_num(dt)}, {_num(kf)}, {_num(kr)}, {_num(kd)})\nassert np.allclose(out, c)",
                    f"c = np.array([0.7, 0.9, 0.2]); before = c.copy(); out = {rk}(c, {_num(dt*0.5)}, {_num(kf)}, {_num(kr)}, {_num(kd)})\nassert np.array_equal(c, before)",
                ],
                "return concentrations_next",
                "RK4 evaluates the nonlinear vector field at midpoint states, giving "
                "fourth-order local accuracy for smooth kinetics.  The implementation "
                "must keep the input state immutable so later trajectory steps are clear.",
            ),
            _step(
                problem_id,
                3,
                f"Integrate the network for a fixed number of steps and return the trajectory, final concentrations, and the maximum moiety-balance residual.",
                f"def {integrate}(initial: np.ndarray, dt: float, steps: int, k_forward: float, k_reverse: float, k_loss: float) -> tuple[np.ndarray, np.ndarray, float]:",
                [
                    f"initial = np.array([1.0, 0.8, 0.0]); out = {integrate}(initial, {_num(dt)}, 12, {_num(kf)}, {_num(kr)}, {_num(kd)})\nassert np.allclose(np.asarray(out), np.asarray(target))",
                    f"initial = np.array([0.0, 0.0, 0.0]); out = {integrate}(initial, {_num(dt)}, 4, {_num(kf)}, {_num(kr)}, {_num(kd)})\nassert out[0].shape[0] == 5 and np.allclose(out[1], 0.0)",
                    f"initial = np.array([0.4, 1.1, 0.2]); out = {integrate}(initial, {_num(dt*0.4)}, 20, {_num(kf)}, {_num(kr)}, {_num(kd)})\nassert np.all(np.isfinite(np.asarray(out)))",
                ],
                "return trajectory, final_concentrations, max_balance_residual",
                "The reaction trajectory is useful only when the stoichiometric "
                "balance is tracked alongside concentrations.  A residual report "
                "exposes sign errors that can otherwise look numerically stable.",
            ),
        ],
        [
            "assert np.all(np.isfinite(trajectory))",
            "assert np.all(trajectory >= -tolerance)",
            "assert max_balance_residual >= 0",
        ],
        "Reversible reaction networks appear in chemical kinetics and biochemical "
        "pathway models.  The problem combines nonlinear mass action, time integration, "
        "and a physically interpretable balance diagnostic.",
    )


def _spectral(problem_id: str, rng: Random) -> dict[str, object]:
    n = rng.choice([256, 384, 512, 768])
    fs = rng.choice([80.0, 100.0, 125.0, 200.0])
    segment = rng.choice([64, 96, 128])
    peak = rng.uniform(7.0, 22.0)
    window = _fname("windowed_fft", problem_id)
    welch = _fname("welch_cross_spectrum", problem_id)
    coherence = _fname("coherence_peak_diagnostics", problem_id)
    return _problem(
        problem_id,
        f"Welch coherence and sub-bin spectral peak estimation [{problem_id}]",
        "Analyze two noisy sampled signals from a coupled oscillator.  Construct a "
        "windowed Fourier segment, average cross spectra with Welch's method, and "
        "extract coherence and a refined peak diagnostic.",
        f"Signals have n={n} samples at fs={_num(fs)} Hz. Segment length is {segment}; "
        f"the expected coupled band is near {_num(peak)} Hz. Return real frequencies "
        "and complex spectra with consistent one-sided normalization.",
        "import numpy as np",
        [
            _step(
                problem_id,
                1,
                "Remove the sample mean, apply a periodic Hann window, and return the one-sided frequency grid and complex real-FFT coefficients with the window power normalization.",
                f"def {window}(signal: np.ndarray, sample_rate: float) -> tuple[np.ndarray, np.ndarray, float]:",
                [
                    f"signal = np.sin(2*np.pi*{_num(peak)}*np.arange({n})/{_num(fs)}); out = {window}(signal, {_num(fs)})\nassert out[0].ndim == 1 and out[1].ndim == 1 and np.isfinite(out[2])",
                    f"signal = np.ones({n}); out = {window}(signal, {_num(fs)})\nassert np.allclose(out[1][1:], 0.0, atol=1e-10)",
                    f"rng = np.random.default_rng(3); signal = rng.normal(size={n}); out = {window}(signal, {_num(fs)})\nassert np.allclose(np.asarray(out), np.asarray(target))",
                ],
                "return frequencies, spectrum, window_power",
                "Windowing controls spectral leakage.  A one-sided real FFT has a "
                "specific frequency ordering and requires consistent treatment of "
                "the DC and Nyquist bins when power is later computed.",
            ),
            _step(
                problem_id,
                2,
                f"Estimate auto- and cross-spectra by averaging overlapping segments of two signals. Reuse {window}, detrend each segment, and return frequency, Pxx, Pyy, and Pxy arrays.",
                f"def {welch}(x: np.ndarray, y: np.ndarray, sample_rate: float, segment_length: int, overlap: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:",
                [
                    f"t = np.arange({n})/{_num(fs)}; x = np.sin(2*np.pi*{_num(peak)}*t); y = np.cos(2*np.pi*{_num(peak)}*t); out = {welch}(x, y, {_num(fs)}, {segment}, {segment//2})\nassert len(out[0]) == len(out[1]) == len(out[2]) == len(out[3])",
                    f"rng = np.random.default_rng(4); x = rng.normal(size={n}); y = rng.normal(size={n}); out = {welch}(x, y, {_num(fs)}, {segment}, {segment//4})\nassert np.all(np.isfinite(np.asarray(out)))",
                    f"x = np.zeros({n}); y = np.zeros({n}); out = {welch}(x, y, {_num(fs)}, {segment}, 0)\nassert np.allclose(np.asarray(out), np.asarray(target))",
                ],
                "return frequencies, auto_x, auto_y, cross_xy",
                "Welch averaging trades frequency resolution for variance reduction. "
                "The cross-spectrum retains phase information, so conjugation and "
                "segment normalization must be handled consistently.",
            ),
            _step(
                problem_id,
                3,
                f"Compute magnitude-squared coherence, locate the strongest interior peak, and return the peak frequency, coherence value, and a local quadratic refinement.",
                f"def {coherence}(frequencies: np.ndarray, auto_x: np.ndarray, auto_y: np.ndarray, cross_xy: np.ndarray) -> tuple[float, float, float]:",
                [
                    f"t = np.arange({n})/{_num(fs)}; x = np.sin(2*np.pi*{_num(peak)}*t); y = x + 0.1*np.cos(2*np.pi*{_num(peak*1.7)}*t); f, xx, yy, xy = {welch}(x, y, {_num(fs)}, {segment}, {segment//2}); out = {coherence}(f, xx, yy, xy)\nassert np.allclose(np.asarray(out), np.asarray(target))",
                    f"f = np.linspace(0.0, {_num(fs/2)}, 20); xx = np.ones(20); yy = np.ones(20); xy = np.zeros(20, dtype=complex); out = {coherence}(f, xx, yy, xy)\nassert np.isfinite(np.asarray(out)).all()",
                    f"f = np.linspace(0.0, 10.0, 5); xx = np.array([0, 1, 4, 1, 0.]); yy = xx.copy(); xy = xx.astype(complex); out = {coherence}(f, xx, yy, xy)\nassert 0.0 <= out[1] <= 1.0",
                ],
                "return peak_frequency, peak_coherence, refined_frequency",
                "Magnitude-squared coherence is bounded by one for valid spectral "
                "matrices.  Interpolating the log or linear neighborhood around a "
                "peak gives a sub-bin estimate of the coupled oscillator frequency.",
            ),
        ],
        [
            "assert np.all(np.diff(frequencies) > 0)",
            "assert np.all((coherence_values >= 0) & (coherence_values <= 1))",
            "assert abs(refined_frequency - peak_frequency) <= bin_width",
        ],
        "Spectral coherence is used to identify shared dynamics in neuroscience, "
        "structural vibration, and climate signals.  The staged task tests FFT "
        "normalization, complex cross-products, and bounded diagnostics.",
    )


def _photon(problem_id: str, rng: Random) -> dict[str, object]:
    background = rng.uniform(0.3, 2.0)
    exposure = rng.uniform(10.0, 60.0)
    counts = rng.choice([12, 20, 32, 48])
    loglike = _fname("poisson_log_likelihood", problem_id)
    mle = _fname("poisson_signal_mle", problem_id)
    interval = _fname("poisson_uncertainty_report", problem_id)
    return _problem(
        problem_id,
        f"Poisson photon-count inference with uncertain background [{problem_id}]",
        "Infer a nonnegative source rate from binned photon counts with a known "
        "exposure and background rate.  Implement the Poisson log-likelihood, a "
        "constrained Newton optimizer, and a curvature-based uncertainty report.",
        f"There are {counts} independent bins and exposure={_num(exposure)} seconds. "
        f"The background rate is {_num(background)} counts/second. Inputs are float "
        "arrays of observed counts; return rates in counts/second and log-likelihood "
        "values as Python floats.",
        "import math\nimport numpy as np",
        [
            _step(
                problem_id,
                1,
                "Evaluate the Poisson log-likelihood for a common nonnegative source rate across bins, including the factorial term and known background contribution.",
                f"def {loglike}(counts: np.ndarray, source_rate: float, background_rate: float, exposure: float) -> float:",
                [
                    f"counts = np.array([0, 2, 4, 1, 3.]); out = {loglike}(counts, 0.7, {_num(background)}, {_num(exposure)})\nassert np.allclose(out, target)",
                    f"counts = np.zeros({counts}); out = {loglike}(counts, 0.0, {_num(background)}, {_num(exposure)})\nassert np.isfinite(out)",
                    f"counts = np.arange(1, 7, dtype=float); out = {loglike}(counts, 1.3, 0.2, 12.0)\nassert np.isfinite(out)",
                ],
                "return log_likelihood",
                "For Poisson data, log likelihood is n log(lambda) - lambda - log(n!). "
                "Using log-gamma for the factorial avoids overflow and keeps zero-count "
                "bins well defined.",
            ),
            _step(
                problem_id,
                2,
                f"Find the nonnegative maximum-likelihood source rate using the score and observed curvature of {loglike}. Handle a boundary optimum when the score at zero is nonpositive.",
                f"def {mle}(counts: np.ndarray, background_rate: float, exposure: float, tolerance: float = 1e-10, max_iterations: int = 100) -> tuple[float, float, int]:",
                [
                    f"counts = np.array([3, 4, 2, 5.]); out = {mle}(counts, {_num(background)}, {_num(exposure)})\nassert np.allclose(np.asarray(out), np.asarray(target))",
                    f"counts = np.zeros({counts}); out = {mle}(counts, {_num(background)}, {_num(exposure)})\nassert out[0] == 0.0 and out[2] <= 100",
                    f"counts = np.array([10., 0., 4., 2.]); out = {mle}(counts, 0.1, 20.0)\nassert out[0] >= 0 and np.isfinite(out[1])",
                ],
                "return source_rate_hat, log_likelihood_at_hat, iterations",
                "The Poisson score is a monotone function of a shared source rate. "
                "A constrained optimum can occur at zero, so an unconstrained Newton "
                "step must be projected or bracketed before convergence is declared.",
            ),
            _step(
                problem_id,
                3,
                f"Return a one-standard-error interval and diagnostics for the fitted rate using the observed Fisher curvature, along with a likelihood-ratio statistic against zero source.",
                f"def {interval}(counts: np.ndarray, background_rate: float, exposure: float) -> tuple[float, float, float, float]:",
                [
                    f"counts = np.array([4., 3., 5., 2.]); out = {interval}(counts, {_num(background)}, {_num(exposure)})\nassert np.allclose(np.asarray(out), np.asarray(target))",
                    f"counts = np.zeros({counts}); out = {interval}(counts, {_num(background)}, {_num(exposure)})\nassert np.isfinite(np.asarray(out)).all()",
                    f"counts = np.array([20., 18., 25., 21.]); out = {interval}(counts, 0.5, 30.0)\nassert out[1] >= 0 and out[2] >= 0",
                ],
                "return rate_hat, standard_error, lower_bound, likelihood_ratio",
                "Curvature gives a local uncertainty scale, while a likelihood-ratio "
                "comparison is more informative near a nonnegative boundary.  Both "
                "statistics must use the same exposure and background convention.",
            ),
        ],
        [
            "assert rate_hat >= 0",
            "assert standard_error >= 0",
            "assert lower_bound <= rate_hat",
        ],
        "Photon counting underlies fluorescence, astronomy, and radiation imaging. "
        "The task connects a discrete probability model to constrained numerical "
        "optimization and uncertainty quantification.",
    )


def _crystal(problem_id: str, rng: Random) -> dict[str, object]:
    a = rng.uniform(2.5, 5.5)
    wavelength = rng.uniform(0.8, 2.2)
    reciprocal = _fname("reciprocal_basis", problem_id)
    factor = _fname("complex_structure_factor", problem_id)
    pattern = _fname("powder_diffraction_pattern", problem_id)
    return _problem(
        problem_id,
        f"Reciprocal-lattice structure factors and powder diffraction [{problem_id}]",
        "Build a crystallographic calculation for a non-orthogonal unit cell. "
        "Construct reciprocal basis vectors, evaluate complex structure factors for "
        "fractional atomic positions, and synthesize a powder diffraction pattern.",
        f"Lattice lengths are in angstroms with a representative scale near {_num(a)}; "
        f"the incident wavelength is {_num(wavelength)} angstroms. Positions are fractional "
        "coordinates and scattering factors may be complex-valued arrays.",
        "import numpy as np",
        [
            _step(
                problem_id,
                1,
                "Compute the reciprocal basis from three direct-lattice vectors using the 2*pi cross-product convention. Return a 3x3 matrix whose rows are reciprocal vectors.",
                f"def {reciprocal}(direct_vectors: np.ndarray) -> np.ndarray:",
                [
                    f"direct = np.array([[{_num(a)},0.0,0.0],[0.3,{_num(a*1.1)},0.0],[0.2,0.4,{_num(a*0.9)}]])\nout = {reciprocal}(direct)\nassert out.shape == (3, 3) and np.allclose(out, target)",
                    f"direct = np.eye(3)*{_num(a)}\nout = {reciprocal}(direct)\nassert np.allclose(out, 2*np.pi* np.eye(3)/{_num(a)})",
                    f"direct = np.array([[2.0,0.1,0.0],[0.0,3.0,0.2],[0.1,0.0,4.0]])\nout = {reciprocal}(direct)\nassert np.all(np.isfinite(out))",
                ],
                "return reciprocal_vectors",
                "Reciprocal vectors follow from the volume triple product.  The "
                "2*pi convention makes a dot product with a fractional reciprocal "
                "index produce a physical scattering vector.",
            ),
            _step(
                problem_id,
                2,
                f"Evaluate the complex structure factor for a list of Miller indices, fractional positions, and per-atom scattering amplitudes. Reuse {reciprocal} and include phase factors exp(i G dot r).",
                f"def {factor}(miller_indices: np.ndarray, fractional_positions: np.ndarray, amplitudes: np.ndarray, reciprocal_vectors: np.ndarray) -> np.ndarray:",
                [
                    f"hkl = np.array([[1,0,0],[1,1,0],[2,1,1]]); pos = np.array([[0,0,0],[0.5,0.5,0.5]]); amp = np.array([1.0, 0.8]); b = {reciprocal}(np.eye(3)*{_num(a)}); out = {factor}(hkl, pos, amp, b)\nassert np.allclose(out, target)",
                    f"hkl = np.zeros((2,3), dtype=int); pos = np.array([[0.1,0.2,0.3]]); amp = np.array([2.0]); b = {reciprocal}(np.eye(3)*3.0); out = {factor}(hkl, pos, amp, b)\nassert np.allclose(out, 2.0)",
                    f"hkl = np.array([[3,2,1]]); pos = np.array([[0,0,0],[0.25,0.5,0.75]]); amp = np.array([1+0j, 0.4-0.2j]); b = {reciprocal}(np.eye(3)*2.8); out = {factor}(hkl, pos, amp, b)\nassert out.dtype.kind == 'c'",
                ],
                "return structure_factors",
                "A structure factor is a coherent sum, not an intensity sum.  Atomic "
                "positions contribute phases determined by the reciprocal scattering "
                "vector, producing systematic absences and interference.",
            ),
            _step(
                problem_id,
                3,
                f"Generate a powder pattern by converting structure-factor magnitudes to intensities, applying the Bragg angle condition, and accumulating a normalized histogram over 2-theta bins.",
                f"def {pattern}(miller_indices: np.ndarray, fractional_positions: np.ndarray, amplitudes: np.ndarray, direct_vectors: np.ndarray, wavelength: float, two_theta_bins: np.ndarray) -> tuple[np.ndarray, np.ndarray]:",
                [
                    f"hkl = np.array([[1,0,0],[1,1,0],[2,0,0]]); pos = np.array([[0,0,0],[0.5,0.5,0.5]]); amp = np.array([1.0, 0.7]); bins = np.linspace(0.0, np.pi, 40); out = {pattern}(hkl, pos, amp, np.eye(3)*{_num(a)}, {_num(wavelength)}, bins)\nassert np.allclose(np.asarray(out), np.asarray(target))",
                    f"hkl = np.array([[1,0,0]]); pos = np.array([[0,0,0]]); amp = np.array([1.0]); bins = np.linspace(0.0, np.pi, 20); out = {pattern}(hkl, pos, amp, np.eye(3)*3.0, {_num(wavelength)}, bins)\nassert out[0].shape == bins.shape and out[1].shape == bins.shape",
                    f"hkl = np.empty((0,3), dtype=int); pos = np.empty((0,3)); amp = np.empty(0); bins = np.linspace(0, 3.14, 10); out = {pattern}(hkl, pos, amp, np.eye(3)*3.0, 1.0, bins)\nassert np.allclose(out[1], 0.0)",
                ],
                "return bin_centers, intensities",
                "Powder diffraction collapses orientation-dependent reflections onto "
                "a one-dimensional angle axis.  Bragg's law imposes a physical domain "
                "on d-spacings and wavelength; intensities are squared magnitudes.",
            ),
        ],
        [
            "assert np.all(intensities >= 0)",
            "assert len(bin_centers) == len(intensities)",
            "assert np.all(np.isfinite(intensities))",
        ],
        "Reciprocal-space calculations connect crystal geometry to measurable powder "
        "patterns in materials science.  The staged problem tests vector geometry, "
        "complex interference, and physically constrained binning.",
    )


def _beam(problem_id: str, rng: Random) -> dict[str, object]:
    elements = rng.choice([4, 6, 8, 10])
    length = rng.uniform(0.8, 2.4)
    young = rng.uniform(50e9, 220e9)
    density = rng.uniform(1500.0, 7800.0)
    dof = _fname("beam_element_matrices", problem_id)
    assemble = _fname("assemble_beam_system", problem_id)
    modes = _fname("cantilever_modal_frequencies", problem_id)
    return _problem(
        problem_id,
        f"Euler-Bernoulli beam finite elements and modal frequencies [{problem_id}]",
        "Construct a one-dimensional Euler-Bernoulli beam model. Derive an element "
        "stiffness and consistent mass matrix, assemble a constrained global system, "
        "and compute natural frequencies and normalized mode shapes.",
        f"The beam has {elements} equal elements over length {_num(length)} m, Young's "
        f"modulus {_num(young)} Pa, density {_num(density)} kg/m^3, and a rectangular "
        "section supplied by the caller. Transverse displacement and rotation are the "
        "two degrees of freedom per node.",
        "import numpy as np",
        [
            _step(
                problem_id,
                1,
                "Return the 4x4 Euler-Bernoulli element stiffness and consistent mass matrices for an element of length le, flexural rigidity EI, and mass-per-length rhoA.",
                f"def {dof}(element_length: float, flexural_rigidity: float, mass_per_length: float) -> tuple[np.ndarray, np.ndarray]:",
                [
                    f"out = {dof}({_num(length/elements)}, {_num(young*1e-8)}, {_num(density*1e-4)})\nassert np.allclose(np.asarray(out), np.asarray(target))",
                    f"out = {dof}(1.0, 2.0, 3.0)\nassert out[0].shape == (4,4) and out[1].shape == (4,4)\nassert np.allclose(out[0], out[0].T) and np.allclose(out[1], out[1].T)",
                    f"out = {dof}(0.4, 10.0, 0.2)\nassert np.all(np.linalg.eigvalsh(out[1]) > 0)",
                ],
                "return stiffness_matrix, mass_matrix",
                "The cubic Hermite beam element has a stiffness matrix proportional "
                "to EI/le^3 and a consistent mass matrix proportional to rhoA*le. "
                "Symmetry and positive mass are physical invariants.",
            ),
            _step(
                problem_id,
                2,
                f"Assemble the global stiffness and mass matrices for {elements} equal elements and eliminate the clamped displacement and rotation degrees of freedom. Reuse {dof}.",
                f"def {assemble}(element_count: int, total_length: float, flexural_rigidity: float, mass_per_length: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:",
                [
                    f"out = {assemble}({elements}, {_num(length)}, {_num(young*1e-8)}, {_num(density*1e-4)})\nassert out[0].shape == out[1].shape and out[2].ndim == 1\nassert np.allclose(np.asarray(out), np.asarray(target))",
                    f"out = {assemble}(2, 1.0, 3.0, 0.4)\nassert out[0].shape == (4,4) and len(out[2]) == 4",
                    f"out = {assemble}(1, 0.8, 5.0, 0.7)\nassert np.all(np.isfinite(np.asarray(out)))",
                ],
                "return reduced_stiffness, reduced_mass, free_dofs",
                "A clamped beam removes two degrees of freedom at the root.  Assembly "
                "must preserve element connectivity and produce matrices suitable for "
                "a generalized eigenproblem.",
            ),
            _step(
                problem_id,
                3,
                f"Solve the reduced generalized eigenproblem and return the first requested natural frequencies and mass-normalized mode shapes.",
                f"def {modes}(element_count: int, total_length: float, flexural_rigidity: float, mass_per_length: float, mode_count: int) -> tuple[np.ndarray, np.ndarray]:",
                [
                    f"out = {modes}({elements}, {_num(length)}, {_num(young*1e-8)}, {_num(density*1e-4)}, 3)\nassert np.allclose(np.asarray(out), np.asarray(target))",
                    f"out = {modes}(2, 1.0, 2.0, 0.5, 1)\nassert out[0].shape == (1,) and out[1].shape[1] == 1",
                    f"out = {modes}(4, 1.5, 10.0, 1.0, 2)\nassert np.all(np.diff(out[0]) > 0)",
                ],
                "return frequencies, mode_shapes",
                "Natural frequencies are square roots of generalized eigenvalues. "
                "Mass-normalization makes mode vectors comparable across meshes, while "
                "the clamped boundary removes rigid-body modes.",
            ),
        ],
        [
            "assert np.all(frequencies > 0)",
            "assert frequencies.shape[0] == mode_shapes.shape[1]",
            "assert np.all(np.isfinite(mode_shapes))",
        ],
        "Finite-element modal analysis is used for structural dynamics and vibration "
        "testing.  The problem requires derivation, matrix assembly, constraints, and "
        "a generalized eigenanalysis rather than a single formula.",
    )


@dataclass(frozen=True)
class Blueprint:
    name: str
    builder: Callable[[str, Random], dict[str, object]]


BLUEPRINTS: tuple[Blueprint, ...] = (
    Blueprint("anisotropic_diffusion", _diffusion),
    Blueprint("kepler_orbit", _kepler),
    Blueprint("reaction_network", _reaction),
    Blueprint("spectral_coherence", _spectral),
    Blueprint("photon_poisson", _photon),
    Blueprint("crystal_structure", _crystal),
    Blueprint("beam_modal", _beam),
)


def build_blueprint(problem_id: str, index: int, seed: int = 0) -> tuple[dict[str, object], str]:
    """Build one deterministic query and return it with its blueprint name."""
    blueprint = BLUEPRINTS[index % len(BLUEPRINTS)]
    rng = Random((seed + 1) * 1_000_003 + index * 97_409)
    return blueprint.builder(problem_id, rng), blueprint.name


__all__ = ["BLUEPRINTS", "Blueprint", "build_blueprint"]
