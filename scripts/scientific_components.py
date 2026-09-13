"""Hand-authored scientific components for SciCode-style query synthesis.

This module contains the question-side catalog only.  It deliberately does
not contain a reference implementation, numerical oracle, model client, or
network access.  A recipe is a small, reviewable scientific contract: a
phenomenon, a numerical/statistical method, an observation scenario, and an
ordered set of executable subproblem contracts.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from random import Random
from typing import Callable, Mapping

try:  # Works as both ``python scripts/...`` and ``scripts....``.
    from .query_schema import validate_query
except ImportError:  # pragma: no cover
    from query_schema import validate_query


def _num(value: float) -> str:
    return f"{value:.7g}"


def _expand(template: str, context: Mapping[str, object]) -> str:
    """Expand ``{{name}}`` markers without interpreting Python braces."""

    value = template
    for key, item in context.items():
        value = value.replace("{{" + key + "}}", str(item))
    unresolved = re.findall(r"\{\{([A-Za-z0-9_]+)\}\}", value)
    if unresolved:
        raise ValueError(f"unresolved component marker(s): {', '.join(unresolved)}")
    return value


def _normalize(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[a-z0-9_-]{8,}", "token", value)
    value = re.sub(r"[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", "num", value)
    return " ".join(value.split())


@dataclass(frozen=True)
class Provenance:
    """Reference metadata; no source code is copied into a generated query."""

    source_kind: str
    source_title: str
    source_url: str
    license: str = "not-applicable: no source code copied"


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    description: str
    background: str
    test_hint: str


@dataclass(frozen=True)
class Diagnostic:
    key: str
    label: str
    description: str


@dataclass(frozen=True)
class MethodMode:
    key: str
    label: str
    description: str
    background: str


TestFactory = Callable[[Mapping[str, object]], tuple[str, ...]]
ParameterFactory = Callable[[Random], Mapping[str, object]]


@dataclass(frozen=True)
class StepComponent:
    key: str
    description: str
    function_header: str
    test_templates: tuple[str, ...]
    return_line: str
    background: str


@dataclass(frozen=True)
class ProblemRecipe:
    key: str
    domain: str
    subdomain: str
    title: str
    description: str
    problem_io: str
    dependencies: str
    background: str
    parameter_factory: ParameterFactory
    steps: tuple[StepComponent, ...]
    modes: tuple[MethodMode, ...]
    scenario_keys: tuple[str, ...]
    diagnostic_keys: tuple[str, ...]
    provenance: Provenance
    tags: tuple[str, ...]


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "periodic",
        "periodic boundaries",
        "Use a periodic domain and make the wraparound convention explicit.",
        "Periodic boundaries remove edge artifacts and make discrete conservation checks meaningful.",
        "Include a constant field and a shifted copy to test wraparound.",
    ),
    Scenario(
        "bounded",
        "bounded observations",
        "Use a bounded interval or finite sample and state how end points are treated.",
        "A finite observation window requires a documented boundary or endpoint convention.",
        "Exercise both an interior case and a case touching the first or last sample.",
    ),
    Scenario(
        "noisy",
        "heteroscedastic noisy observations",
        "Assume observations carry known, nonuniform noise scales and keep the weighting visible.",
        "Noise weights alter the objective; reporting residuals alone is insufficient without the stated uncertainty model.",
        "Use one low-noise and one high-noise observation so weighting can be detected.",
    ),
    Scenario(
        "irregular",
        "irregular sampling",
        "Accept strictly increasing but nonuniform sample locations instead of silently assuming a grid.",
        "Irregular sampling changes interpolation, quadrature, and spectral normalization choices.",
        "Pass a deliberately nonuniform coordinate array and verify ordering is preserved.",
    ),
    Scenario(
        "sparse",
        "sparse events",
        "Represent the input as a short event list or sparse observation set and define empty-bin behavior.",
        "Sparse scientific observations need explicit zero-count and duplicate-event semantics.",
        "Include an empty bin and repeated coordinates in separate tests.",
    ),
    Scenario(
        "positive",
        "positive constrained states",
        "Keep concentrations, probabilities, or intensities nonnegative while documenting the numerical safeguard.",
        "Physical positivity is a constraint, not a cosmetic clipping operation; the returned state must remain interpretable.",
        "Test a near-zero component and a state with one dominant component.",
    ),
    Scenario(
        "multichannel",
        "multichannel measurements",
        "Process several synchronized channels with a clearly stated channel axis.",
        "Channel order and axis conventions are part of the scientific interface.",
        "Use two channels with different amplitudes and verify channel-wise output shapes.",
    ),
    Scenario(
        "stochastic",
        "repeated stochastic trials",
        "Use an explicit random generator or seed and report a statistic over repeated trials.",
        "Reproducible stochastic experiments require an isolated random stream and a declared estimator.",
        "Run the same seed twice and compare the deterministic summary.",
    ),
    Scenario(
        "anisotropic",
        "anisotropic coefficients",
        "Use direction-dependent coefficients and state the axis ordering in every array.",
        "Anisotropy makes axis transpositions observable and prevents a scalar shortcut.",
        "Swap the coefficient pair and verify the directional response changes.",
    ),
    Scenario(
        "stiff",
        "stiff rate scales",
        "Include separated time scales and require a stable update or an explicit step-size policy.",
        "Stiffness exposes methods that appear correct at one step size but become unstable over a trajectory.",
        "Use a short stable step and a longer diagnostic trajectory.",
    ),
    Scenario(
        "calibrated",
        "calibrated reference scale",
        "Normalize the computed quantity against a stated reference scale before reporting diagnostics.",
        "Calibration must be applied after unit conversion and before threshold comparisons.",
        "Include a unit-scale input and a non-unit reference to catch missing normalization.",
    ),
    Scenario(
        "ensemble",
        "ensemble summaries",
        "Return both a point estimate and an uncertainty summary over independent replicates.",
        "An ensemble estimate is incomplete without the number of replicates and the aggregation convention.",
        "Use unequal replicate lengths and verify the declared aggregation rule.",
    ),
)


SCENARIO_MAP = {item.key: item for item in SCENARIOS}


DIAGNOSTICS: tuple[Diagnostic, ...] = (
    Diagnostic("invariant", "invariant residual", "Report a physically motivated invariant residual."),
    Diagnostic("stability", "stability margin", "Report a stability or conditioning margin."),
    Diagnostic("uncertainty", "uncertainty estimate", "Return an uncertainty or interval alongside the estimate."),
    Diagnostic("spectrum", "spectral summary", "Return a spectral peak, bandwidth, or mode-power summary."),
    Diagnostic("likelihood", "likelihood diagnostic", "Return a log-likelihood or calibrated objective value."),
    Diagnostic("sensitivity", "parameter sensitivity", "Report a finite-difference or local sensitivity quantity."),
    Diagnostic("calibration", "calibration residual", "Report the residual after applying the declared calibration."),
    Diagnostic("robustness", "perturbation robustness", "Compare the result under a controlled perturbation."),
)


DIAGNOSTIC_MAP = {item.key: item for item in DIAGNOSTICS}


DESIGN_VARIANTS: tuple[tuple[str, str], ...] = (
    ("forward", "Prioritize a forward simulation and report the state after the declared interval."),
    ("inverse", "Treat the final diagnostic as an inverse check on the supplied parameters."),
    ("edge", "Include an edge-of-domain or limiting-value interpretation in the numerical contract."),
    ("scale", "Require the implementation to remain well-scaled when all physical units are multiplied by a common factor."),
    ("symmetry", "Use a symmetry or exchange argument as an additional scientific consistency check."),
    ("perturbation", "Compare the nominal result with a small controlled perturbation of one input."),
    ("convergence", "Make the requested diagnostic sensitive to refinement of the grid, sample count, or step size."),
    ("conservation", "Emphasize a conserved quantity and report its normalized residual."),
    ("conditioning", "Expose a conditioning or near-degeneracy quantity rather than only the central estimate."),
    ("sparse_case", "Make sparse or zero-valued observations a first-class case in the stated workflow."),
    ("noise_weight", "Require uncertainty weights to propagate into both the estimate and the reported diagnostic."),
    ("boundary_case", "Contrast an interior case with the relevant boundary convention."),
    ("multi_scale", "Use separated scales and require the output to preserve the requested physical ordering."),
    ("replicate", "Use repeated independent trials and distinguish a point estimate from between-trial variation."),
    ("unit_check", "State a dimensional check that can detect a missing conversion or normalization."),
    ("cross_check", "Return two independently computed summaries that should agree within tolerance."),
    ("monotonicity", "Use a monotonicity or positivity expectation as a scientific check on the result."),
    ("reproducibility", "Require the declared seed or deterministic convention to reproduce the same summary."),
    ("model_compare", "Compare the selected numerical or statistical mode against a simpler baseline quantity."),
    ("long_horizon", "Evaluate the diagnostic after a longer horizon where small systematic errors accumulate."),
)


DESIGN_VARIANT_MAP = {key: description for key, description in DESIGN_VARIANTS}


def _step(
    key: str,
    description: str,
    function_header: str,
    tests: tuple[str, ...],
    return_line: str,
    background: str,
) -> StepComponent:
    return StepComponent(key, description, function_header, tests, return_line, background)


def _recipe(
    key: str,
    domain: str,
    subdomain: str,
    title: str,
    description: str,
    problem_io: str,
    dependencies: str,
    background: str,
    parameter_factory: ParameterFactory,
    steps: tuple[StepComponent, ...],
    modes: tuple[MethodMode, ...],
    scenarios: tuple[str, ...],
    diagnostics: tuple[str, ...],
    source_title: str,
    source_url: str,
    tags: tuple[str, ...],
) -> ProblemRecipe:
    return ProblemRecipe(
        key=key,
        domain=domain,
        subdomain=subdomain,
        title=title,
        description=description,
        problem_io=problem_io,
        dependencies=dependencies,
        background=background,
        parameter_factory=parameter_factory,
        steps=steps,
        modes=modes,
        scenario_keys=scenarios,
        diagnostic_keys=diagnostics,
        provenance=Provenance("repository-or-paper", source_title, source_url),
        tags=tags,
    )


def _choose(rng: Random, values: tuple[str, ...]) -> str:
    return values[rng.randrange(len(values))]


def _context(
    recipe: ProblemRecipe,
    problem_id: str,
    index: int,
    seed: int,
) -> dict[str, object]:
    rng = Random((seed + 17) * 2_000_033 + index * 131_071)
    scenario = SCENARIO_MAP[_choose(rng, recipe.scenario_keys)]
    diagnostic = DIAGNOSTIC_MAP[_choose(rng, recipe.diagnostic_keys)]
    mode = recipe.modes[rng.randrange(len(recipe.modes))]
    variant_token = hashlib.sha256(
        f"{seed}:{index}:design-variant".encode("utf-8")
    ).digest()
    variant_index = int.from_bytes(variant_token[:8], "big") % len(DESIGN_VARIANTS)
    variant_key, variant_description = DESIGN_VARIANTS[variant_index]
    params = dict(recipe.parameter_factory(rng))
    suffix = problem_id.replace("-", "_")
    context: dict[str, object] = {
        "problem_id": problem_id,
        "suffix": suffix,
        "scenario_key": scenario.key,
        "scenario_label": scenario.label,
        "scenario_description": scenario.description,
        "scenario_background": scenario.background,
        "scenario_test_hint": scenario.test_hint,
        "diagnostic_key": diagnostic.key,
        "diagnostic_label": diagnostic.label,
        "diagnostic_description": diagnostic.description,
        "mode_key": mode.key,
        "mode_label": mode.label,
        "mode_description": mode.description,
        "mode_background": mode.background,
        "variant_key": variant_key,
        "variant_description": variant_description,
        "recipe_key": recipe.key,
        **params,
    }
    for step in recipe.steps:
        context[f"fn_{step.key}"] = (
            f"{recipe.key}_{mode.key}_{scenario.key}_{step.key}_{suffix}"
        )
    return context


def _render_recipe(
    recipe: ProblemRecipe,
    problem_id: str,
    index: int,
    seed: int,
) -> tuple[dict[str, object], dict[str, object]]:
    context = _context(recipe, problem_id, index, seed)
    context["title"] = _expand(recipe.title, context)
    context["description"] = _expand(recipe.description, context)
    context["problem_io"] = _expand(recipe.problem_io, context)
    context["background"] = _expand(recipe.background, context)
    context["dependencies"] = recipe.dependencies

    step_values: list[dict[str, object]] = []
    for step_number, step in enumerate(recipe.steps, start=1):
        local = dict(context)
        local["fn"] = context[f"fn_{step.key}"]
        local["step_index"] = step_number
        tests = [_expand(template, local) for template in step.test_templates]
        # Keep the final oracle-bearing case and vary the number of public cases.
        case_count = 2 + ((index + step_number + len(str(context["mode_key"]))) % 3)
        case_count = min(case_count, len(tests))
        if case_count < len(tests):
            tests = tests[: case_count - 1] + [tests[-1]]
        step_values.append(
            {
                "step_number": f"{problem_id}.{step_number}",
                "step_description_prompt": _expand(step.description, local),
                "function_header": _expand(step.function_header, local),
                "test_cases": tests,
                "return_line": _expand(step.return_line, local),
                "step_background": _expand(step.background, local),
            }
        )

    query = {
        "problem_name": f"{_expand(recipe.title, context)} [{problem_id}]",
        "problem_id": problem_id,
        "problem_description_main": _expand(
            recipe.description + " " + context["variant_description"].__str__(),
            context,
        ),
        "problem_io": _expand(recipe.problem_io, context),
        "required_dependencies": recipe.dependencies,
        "sub_steps": step_values,
        "general_tests": [
            "assert np.all(np.isfinite(np.asarray(result)))",
            "assert residual <= tolerance",
            "assert np.asarray(result).ndim >= 1",
        ],
        "problem_background_main": _expand(
            recipe.background + " The design focus is {{variant_key}}: {{variant_description}}",
            context,
        ),
    }
    validate_query(query)

    normalized = "|".join(
        [
            recipe.key,
            context["mode_key"].__str__(),
            context["scenario_key"].__str__(),
            context["diagnostic_key"].__str__(),
            context["variant_key"].__str__(),
            str(len(step_values)),
            _normalize(query["problem_description_main"]),
            *(
                _normalize(str(step["step_description_prompt"]))
                for step in step_values
            ),
            ",".join(str(len(step["test_cases"])) for step in step_values),
        ]
    )
    signature = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    metadata = {
        "component": recipe.key,
        "domain": recipe.domain,
        "subdomain": recipe.subdomain,
        "method_mode": context["mode_key"],
        "scenario": context["scenario_key"],
        "diagnostic": context["diagnostic_key"],
        "composition_variant": context["variant_key"],
        "tags": list(recipe.tags),
        "step_count": len(step_values),
        "test_case_counts": [len(step["test_cases"]) for step in step_values],
        "normalized_signature": signature,
        "provenance": {
            "kind": recipe.provenance.source_kind,
            "title": recipe.provenance.source_title,
            "url": recipe.provenance.source_url,
            "license": recipe.provenance.license,
        },
    }
    return query, metadata


# Parameter factories are intentionally small and inspectable.  Numeric values
# make repeated records executable with different scales; the mode/scenario
# components carry the semantic variation.
def _p_heat(rng: Random) -> Mapping[str, object]:
    n = rng.choice((32, 48, 64, 80))
    dx = rng.choice((0.01, 0.02, 0.04))
    alpha = rng.uniform(0.02, 0.3)
    dt = dx * dx / alpha * rng.uniform(0.15, 0.55)
    return {"n": n, "dx": _num(dx), "alpha": _num(alpha), "dt": _num(dt), "theta": _num(rng.choice((0.5, 0.75, 1.0)))}


def _p_wave(rng: Random) -> Mapping[str, object]:
    n = rng.choice((96, 128, 160))
    dx = rng.choice((0.01, 0.02, 0.025))
    speed = rng.uniform(0.4, 1.8)
    dt = dx / speed * rng.uniform(0.25, 0.7)
    return {"n": n, "dx": _num(dx), "speed": _num(speed), "dt": _num(dt), "steps": rng.choice((4, 8, 12))}


def _p_lorentz(rng: Random) -> Mapping[str, object]:
    frequencies = rng.choice((32, 48, 64))
    omega0 = rng.uniform(0.8, 3.0)
    damping = rng.uniform(0.03, 0.25)
    drive = rng.uniform(0.5, 2.0)
    return {"frequencies": frequencies, "omega0": _num(omega0), "damping": _num(damping), "drive": _num(drive)}


def _p_ising(rng: Random) -> Mapping[str, object]:
    size = rng.choice((12, 16, 20, 24))
    beta = rng.uniform(0.25, 0.65)
    sweeps = rng.choice((40, 80, 120))
    return {"size": size, "beta": _num(beta), "sweeps": sweeps, "seed": rng.randrange(10_000, 99_999)}


def _p_nbody(rng: Random) -> Mapping[str, object]:
    count = rng.choice((3, 4, 6, 8))
    dt = rng.uniform(0.001, 0.02)
    softening = rng.uniform(0.005, 0.04)
    return {"count": count, "dt": _num(dt), "softening": _num(softening), "steps": rng.choice((4, 8, 16))}


def _p_reaction(rng: Random) -> Mapping[str, object]:
    return {"kf": _num(rng.uniform(0.2, 2.0)), "kr": _num(rng.uniform(0.03, 0.8)), "kloss": _num(rng.uniform(0.005, 0.2)), "dt": _num(rng.uniform(0.002, 0.03)), "steps": rng.choice((8, 16, 24))}


def _p_enzyme(rng: Random) -> Mapping[str, object]:
    points = rng.choice((8, 12, 16, 20))
    vmax = rng.uniform(0.8, 4.0)
    km = rng.uniform(0.1, 1.5)
    noise = rng.uniform(0.01, 0.15)
    return {"points": points, "vmax": _num(vmax), "km": _num(km), "noise": _num(noise)}


def _p_acid(rng: Random) -> Mapping[str, object]:
    pka1 = rng.uniform(2.0, 4.0)
    pka2 = rng.uniform(5.0, 8.0)
    total = rng.uniform(0.01, 0.2)
    return {"pka1": _num(pka1), "pka2": _num(pka2), "total": _num(total), "grid": rng.choice((64, 96, 128))}


def _p_partition(rng: Random) -> Mapping[str, object]:
    levels = rng.choice((8, 12, 16, 24))
    spacing = rng.uniform(0.05, 0.4)
    return {"levels": levels, "spacing": _num(spacing), "temperature": _num(rng.uniform(0.4, 2.5))}


def _p_crystal(rng: Random) -> Mapping[str, object]:
    atoms = rng.choice((3, 4, 6, 8))
    bins = rng.choice((96, 128, 192))
    wavelength = rng.uniform(0.8, 2.0)
    return {"atoms": atoms, "bins": bins, "wavelength": _num(wavelength), "width": _num(rng.uniform(0.01, 0.08))}


def _p_phonon(rng: Random) -> Mapping[str, object]:
    sites = rng.choice((3, 4, 5, 6))
    coupling = rng.uniform(0.4, 2.5)
    mass = rng.uniform(0.5, 4.0)
    return {"sites": sites, "coupling": _num(coupling), "mass": _num(mass), "qpoints": rng.choice((8, 12, 16))}


def _p_phase(rng: Random) -> Mapping[str, object]:
    n = rng.choice((32, 48, 64))
    epsilon = rng.uniform(0.005, 0.04)
    mobility = rng.uniform(0.2, 1.4)
    return {"n": n, "epsilon": _num(epsilon), "mobility": _num(mobility), "dt": _num(rng.uniform(0.001, 0.02))}


def _p_percolation(rng: Random) -> Mapping[str, object]:
    size = rng.choice((24, 32, 40, 48))
    probability = rng.uniform(0.35, 0.7)
    return {"size": size, "probability": _num(probability), "seed": rng.randrange(10_000, 99_999), "trials": rng.choice((8, 16, 24))}


def _p_lotka(rng: Random) -> Mapping[str, object]:
    return {"alpha": _num(rng.uniform(0.7, 1.8)), "beta": _num(rng.uniform(0.01, 0.08)), "delta": _num(rng.uniform(0.01, 0.08)), "gamma": _num(rng.uniform(0.5, 1.5)), "dt": _num(rng.uniform(0.005, 0.04))}


def _p_sir(rng: Random) -> Mapping[str, object]:
    return {"population": rng.choice((10_000, 50_000, 100_000)), "beta": _num(rng.uniform(0.15, 0.8)), "gamma": _num(rng.uniform(0.05, 0.3)), "days": rng.choice((30, 60, 90))}


def _p_hill(rng: Random) -> Mapping[str, object]:
    points = rng.choice((10, 14, 18, 24))
    return {"points": points, "ec50": _num(rng.uniform(0.2, 4.0)), "hill": _num(rng.uniform(0.8, 3.5)), "top": _num(rng.uniform(0.8, 2.0))}


def _p_wright(rng: Random) -> Mapping[str, object]:
    return {"population": rng.choice((40, 80, 160)), "generations": rng.choice((20, 40, 80)), "initial_frequency": _num(rng.uniform(0.1, 0.9)), "replicates": rng.choice((16, 32, 64))}


def _p_kepler(rng: Random) -> Mapping[str, object]:
    return {"a": _num(rng.uniform(1.0, 5.0)), "eccentricity": _num(rng.uniform(0.05, 0.8)), "mu": _num(rng.uniform(0.6, 2.4)), "dt": _num(rng.uniform(0.05, 0.6))}


def _p_photometry(rng: Random) -> Mapping[str, object]:
    points = rng.choice((64, 96, 128, 192))
    period = rng.uniform(0.4, 8.0)
    return {"points": points, "period": _num(period), "noise": _num(rng.uniform(0.005, 0.08)), "baseline": _num(rng.uniform(10.0, 100.0))}


def _p_seismic(rng: Random) -> Mapping[str, object]:
    layers = rng.choice((3, 4, 5, 6))
    return {"layers": layers, "depth": _num(rng.uniform(10.0, 80.0)), "velocity": _num(rng.uniform(2.0, 8.0)), "offset": _num(rng.uniform(1.0, 30.0))}


def _p_harmonics(rng: Random) -> Mapping[str, object]:
    degree = rng.choice((3, 4, 5, 6))
    samples = rng.choice((96, 144, 192))
    return {"degree": degree, "samples": samples, "radius": _num(rng.uniform(1.0, 4.0)), "noise": _num(rng.uniform(0.0, 0.03))}


def _p_kalman(rng: Random) -> Mapping[str, object]:
    steps = rng.choice((24, 48, 72))
    return {"steps": steps, "process_noise": _num(rng.uniform(0.001, 0.2)), "measurement_noise": _num(rng.uniform(0.01, 0.5)), "dt": _num(rng.uniform(0.05, 0.5))}


def _p_gp(rng: Random) -> Mapping[str, object]:
    points = rng.choice((12, 20, 32, 48))
    return {"points": points, "length_scale": _num(rng.uniform(0.1, 2.0)), "noise": _num(rng.uniform(0.001, 0.2)), "jitter": _num(rng.uniform(1e-9, 1e-5))}


def _p_pca(rng: Random) -> Mapping[str, object]:
    samples = rng.choice((24, 40, 64, 96))
    features = rng.choice((5, 8, 12, 16))
    return {"samples": samples, "features": features, "components": rng.choice((2, 3, 4)), "noise": _num(rng.uniform(0.0, 0.15))}


def _p_changepoint(rng: Random) -> Mapping[str, object]:
    points = rng.choice((64, 96, 128, 192))
    penalty = rng.uniform(0.5, 8.0)
    return {"points": points, "penalty": _num(penalty), "segments": rng.choice((2, 3, 4)), "noise": _num(rng.uniform(0.02, 0.3))}


def _p_mcmc(rng: Random) -> Mapping[str, object]:
    draws = rng.choice((400, 800, 1200))
    proposal = rng.uniform(0.05, 0.8)
    return {"draws": draws, "proposal": _num(proposal), "burn": rng.choice((50, 100, 200)), "seed": rng.randrange(10_000, 99_999)}


def _p_graph(rng: Random) -> Mapping[str, object]:
    nodes = rng.choice((12, 20, 32, 48))
    clusters = rng.choice((2, 3, 4))
    return {"nodes": nodes, "clusters": clusters, "k": rng.choice((3, 5, 7)), "seed": rng.randrange(10_000, 99_999)}


def _p_wavelet(rng: Random) -> Mapping[str, object]:
    points = rng.choice((128, 256, 384))
    level = rng.choice((2, 3, 4))
    return {"points": points, "level": level, "noise": _num(rng.uniform(0.02, 0.3)), "threshold": _num(rng.uniform(0.05, 0.6))}


def _p_robust(rng: Random) -> Mapping[str, object]:
    points = rng.choice((20, 32, 48, 64))
    return {"points": points, "outlier_fraction": _num(rng.uniform(0.05, 0.25)), "delta": _num(rng.uniform(0.5, 2.0)), "bootstrap": rng.choice((32, 64, 96))}


def _p_ar(rng: Random) -> Mapping[str, object]:
    points = rng.choice((96, 128, 192, 256))
    order = rng.choice((2, 3, 4, 5))
    return {"points": points, "order": order, "noise": _num(rng.uniform(0.01, 0.2)), "frequency_bins": rng.choice((64, 96, 128))}


def _mode(key: str, label: str, description: str, background: str) -> MethodMode:
    return MethodMode(key, label, description, background)


RECIPES: tuple[ProblemRecipe, ...] = (
    _recipe(
        "heat_theta", "physics", "computational-physics",
        "Heterogeneous one-dimensional heat transport",
        "Construct and integrate a one-dimensional heat equation with spatially varying diffusivity. The task couples a conservative operator, a theta-family time integrator, and a diagnostic for transport and stability. {{scenario_description}} {{mode_description}}",
        "The temperature is a float array of length {{n}} on a uniform grid with spacing {{dx}}. The diffusivity is positive and varies by cell; use alpha_ref={{alpha}} and dt={{dt}}. Return a new array and scalar diagnostics in the order specified by each step.",
        "import numpy as np\nfrom scipy.linalg import solve_banded",
        "The heat equation is a parabolic conservation law. A variable-coefficient flux must be assembled at cell faces rather than by multiplying a constant-coefficient stencil. {{scenario_background}} {{mode_background}}",
        _p_heat,
        (
            _step("operator", "Build the face-flux discretization of the heterogeneous heat operator. Use the stated boundary convention, keep the input immutable, and make the returned vector have the same length as the input.", "def {{fn}}(temperature: np.ndarray, diffusivity: np.ndarray, spacing: float) -> np.ndarray:", (
                "temperature = np.linspace(0.0, 1.0, {{n}}); diffusivity = np.full({{n}}, {{alpha}})\nout = {{fn}}(temperature, diffusivity, {{dx}})\nassert out.shape == temperature.shape\nassert np.all(np.isfinite(out))",
                "temperature = np.zeros({{n}}); temperature[{{n}} // 3] = 1.0; diffusivity = np.linspace({{alpha}} * 0.5, {{alpha}} * 1.5, {{n}})\nout = {{fn}}(temperature, diffusivity, {{dx}})\nassert np.isclose(out.sum(), 0.0, atol=1e-10)",
                "temperature = np.arange({{n}}, dtype=float); before = temperature.copy(); diffusivity = np.full({{n}}, {{alpha}})\nout = {{fn}}(temperature, diffusivity, {{dx}})\nassert np.array_equal(temperature, before)",
                "temperature = np.sin(np.arange({{n}}) * 0.13); diffusivity = np.linspace({{alpha}} * 0.7, {{alpha}} * 1.2, {{n}})\nout = {{fn}}(temperature, diffusivity, {{dx}})\nassert np.allclose(out, target)",
            ), "return heat_operator", "Face fluxes enforce a discrete balance law. The sign convention and boundary closure determine whether a constant field remains stationary."),
            _step("advance", "Advance the heat equation for a requested number of steps using the selected theta method. Reuse {{fn_operator}}, solve rather than explicitly inverting a matrix, and return a fresh temperature field.", "def {{fn}}(temperature: np.ndarray, diffusivity: np.ndarray, spacing: float, dt: float, steps: int, theta: float) -> np.ndarray:", (
                "temperature = np.zeros({{n}}); temperature[{{n}} // 2] = 1.0; diffusivity = np.full({{n}}, {{alpha}})\nout = {{fn}}(temperature, diffusivity, {{dx}}, {{dt}}, 1, {{theta}})\nassert out.shape == temperature.shape",
                "temperature = np.ones({{n}}); diffusivity = np.full({{n}}, {{alpha}})\nout = {{fn}}(temperature, diffusivity, {{dx}}, {{dt}}, 5, {{theta}})\nassert np.allclose(out, temperature, atol=1e-9)",
                "rng = np.random.default_rng(13); temperature = rng.normal(size={{n}}); diffusivity = np.linspace({{alpha}} * 0.8, {{alpha}} * 1.3, {{n}})\nout = {{fn}}(temperature, diffusivity, {{dx}}, {{dt}}, 3, {{theta}})\nassert np.all(np.isfinite(out))",
                "temperature = np.linspace(1.0, 0.0, {{n}}); diffusivity = np.full({{n}}, {{alpha}})\nout = {{fn}}(temperature, diffusivity, {{dx}}, {{dt}}, 2, {{theta}})\nassert np.allclose(out, target)",
            ), "return temperature_next", "Theta methods interpolate between explicit and implicit updates. The chosen theta controls stability and numerical diffusion."),
            _step("diagnostics", "Run the transport experiment and return the final field, integrated heat, boundary flux, and the requested {{diagnostic_label}}.", "def {{fn}}(temperature0: np.ndarray, diffusivity: np.ndarray, spacing: float, dt: float, steps: int, theta: float) -> tuple[np.ndarray, float, float, float]:", (
                "temperature0 = np.zeros({{n}}); temperature0[{{n}} // 2] = 1.0; diffusivity = np.full({{n}}, {{alpha}})\nout = {{fn}}(temperature0, diffusivity, {{dx}}, {{dt}}, 2, {{theta}})\nassert out[0].shape == temperature0.shape\nassert np.all(np.isfinite(np.asarray(out[1:])))",
                "temperature0 = np.ones({{n}}); diffusivity = np.full({{n}}, {{alpha}})\nout = {{fn}}(temperature0, diffusivity, {{dx}}, {{dt}}, 4, {{theta}})\nassert np.allclose(np.asarray(out[1:]), np.asarray(target[1:]))",
                "rng = np.random.default_rng(17); temperature0 = rng.normal(size={{n}}); diffusivity = np.linspace({{alpha}} * 0.5, {{alpha}} * 1.5, {{n}})\nout = {{fn}}(temperature0, diffusivity, {{dx}}, {{dt}}, 3, {{theta}})\nassert np.all(np.isfinite(np.asarray(out)))",
                "temperature0 = np.linspace(0.0, 1.0, {{n}}); diffusivity = np.full({{n}}, {{alpha}})\nout = {{fn}}(temperature0, diffusivity, {{dx}}, {{dt}}, 1, {{theta}})\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return temperature_final, integrated_heat, boundary_flux, diagnostic", "Integral heat and boundary flux expose mistakes that a pointwise visual check can miss. The final scalar is the selected diagnostic rather than an arbitrary norm."),
        ),
        (_mode("explicit", "forward Euler", "Use a forward Euler predictor with the stated CFL-safe step.", "The explicit update is conditionally stable and must respect the diffusivity-scaled step."), _mode("implicit", "backward Euler", "Use a backward Euler solve for each step.", "Backward Euler damps high-frequency modes and remains stable for stiff diffusion."), _mode("cn", "Crank-Nicolson", "Use the Crank-Nicolson average of old and new fluxes.", "Crank-Nicolson is second order in time but can preserve oscillations when the step is too large.")),
        ("periodic", "bounded", "anisotropic", "stiff"), ("invariant", "stability", "robustness"),
        "SciPy scientific Python routines (reference API)", "https://github.com/scipy/scipy", ("pde", "finite-difference", "conservation"),
    ),
    _recipe(
        "wave_leapfrog", "physics", "computational-physics",
        "Variable-speed one-dimensional wave propagation",
        "Simulate a one-dimensional wave packet in a medium with piecewise wave speed. Build the spatial operator, advance it with a two-level scheme, and estimate propagation and energy diagnostics. {{scenario_description}} {{mode_description}}",
        "The displacement and velocity arrays have length {{n}}, grid spacing {{dx}}, wave speed reference {{speed}}, and time step {{dt}}. The initial pulse is compact and the boundary treatment must be stated by the implementation.",
        "import numpy as np",
        "The wave equation is hyperbolic: a conservative spatial flux and a compatible time staggering are required to avoid artificial energy creation. {{scenario_background}} {{mode_background}}",
        _p_wave,
        (
            _step("stencil", "Construct the variable-speed centered flux operator for the displacement field and return the acceleration. Do not modify either input array.", "def {{fn}}(displacement: np.ndarray, speed: np.ndarray, spacing: float) -> np.ndarray:", (
                "u = np.sin(np.arange({{n}}) * 0.07); c = np.full({{n}}, {{speed}})\nout = {{fn}}(u, c, {{dx}})\nassert out.shape == u.shape",
                "u = np.zeros({{n}}); u[{{n}} // 2] = 1.0; c = np.linspace({{speed}} * 0.8, {{speed}} * 1.2, {{n}})\nout = {{fn}}(u, c, {{dx}})\nassert np.all(np.isfinite(out))",
                "u = np.linspace(0.0, 1.0, {{n}}); c = np.full({{n}}, {{speed}})\nout = {{fn}}(u, c, {{dx}})\nassert np.allclose(out, target)",
            ), "return acceleration", "The divergence of c squared times the displacement gradient is the conservative variable-speed operator."),
            _step("advance", "Advance displacement and velocity for {{steps}} steps using the selected two-level integrator. Reuse {{fn_stencil}} and return both new arrays.", "def {{fn}}(displacement: np.ndarray, velocity: np.ndarray, speed: np.ndarray, spacing: float, dt: float, steps: int) -> tuple[np.ndarray, np.ndarray]:", (
                "u = np.zeros({{n}}); v = np.zeros({{n}}); u[{{n}} // 2] = 1.0; c = np.full({{n}}, {{speed}})\nout = {{fn}}(u, v, c, {{dx}}, {{dt}}, 1)\nassert out[0].shape == u.shape and out[1].shape == v.shape",
                "u = np.ones({{n}}); v = np.zeros({{n}}); c = np.full({{n}}, {{speed}})\nout = {{fn}}(u, v, c, {{dx}}, {{dt}}, {{steps}})\nassert np.all(np.isfinite(np.asarray(out)))",
                "rng = np.random.default_rng(19); u = rng.normal(size={{n}}); v = rng.normal(size={{n}}); c = np.linspace({{speed}} * 0.7, {{speed}} * 1.3, {{n}})\nout = {{fn}}(u, v, c, {{dx}}, {{dt}}, 3)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return displacement_next, velocity_next", "Leapfrog-style schemes conserve a discrete energy only when the spatial and temporal conventions match."),
            _step("diagnostics", "Run the wave experiment and return the final state, pulse centroid, discrete energy, and {{diagnostic_label}}.", "def {{fn}}(displacement0: np.ndarray, velocity0: np.ndarray, speed: np.ndarray, spacing: float, dt: float, steps: int) -> tuple[np.ndarray, np.ndarray, float, float]:", (
                "u = np.zeros({{n}}); v = np.zeros({{n}}); u[{{n}} // 3] = 1.0; c = np.full({{n}}, {{speed}})\nout = {{fn}}(u, v, c, {{dx}}, {{dt}}, 2)\nassert out[0].shape == u.shape and np.isfinite(out[2])",
                "u = np.sin(np.arange({{n}}) * 0.03); v = np.cos(np.arange({{n}}) * 0.03); c = np.full({{n}}, {{speed}})\nout = {{fn}}(u, v, c, {{dx}}, {{dt}}, {{steps}})\nassert np.allclose(np.asarray(out[2:]), np.asarray(target[2:]))",
                "u = np.zeros({{n}}); v = np.zeros({{n}}); c = np.linspace({{speed}} * 0.8, {{speed}} * 1.2, {{n}})\nout = {{fn}}(u, v, c, {{dx}}, {{dt}}, 1)\nassert np.all(np.isfinite(np.asarray(out)))",
            ), "return displacement_final, velocity_final, energy, diagnostic", "Energy and centroid diagnostics distinguish a physically propagating pulse from a numerically damped or unstable field."),
        ),
        (_mode("leapfrog", "leapfrog", "Use a centered leapfrog update with velocity at half steps.", "The centered update is second order but requires a compatible initial half step."), _mode("verlet", "velocity Verlet", "Use velocity Verlet and recompute acceleration after each position update.", "Velocity Verlet keeps position and velocity synchronized and is useful for long trajectories.")),
        ("periodic", "bounded", "anisotropic", "stiff"), ("invariant", "stability", "sensitivity"),
        "NumPy array and wave-equation reference formulations", "https://numpy.org/doc/stable/reference/routines.linalg.html", ("wave", "hyperbolic-pde", "energy"),
    ),
    _recipe(
        "lorentz_response", "physics", "condensed-matter",
        "Driven damped oscillator response curve",
        "Compute the complex susceptibility of a driven damped oscillator over a frequency grid, identify its resonance, and quantify a fitted linewidth. {{scenario_description}} {{mode_description}}",
        "Use {{frequencies}} angular-frequency samples, natural frequency {{omega0}}, damping {{damping}}, and drive amplitude {{drive}}. Frequencies are in consistent inverse-time units and the response is complex-valued.",
        "import numpy as np",
        "The Lorentz oscillator is a minimal model for dielectric and mechanical response. Separating real and imaginary parts is essential because absorption and dispersion peak at different frequencies. {{scenario_background}} {{mode_background}}",
        _p_lorentz,
        (
            _step("susceptibility", "Evaluate the complex steady-state susceptibility at each supplied angular frequency using the selected damping convention.", "def {{fn}}(frequency: np.ndarray, natural_frequency: float, damping: float, drive: float) -> np.ndarray:", (
                "frequency = np.linspace(0.1, 4.0, {{frequencies}}); out = {{fn}}(frequency, {{omega0}}, {{damping}}, {{drive}})\nassert out.shape == frequency.shape and np.iscomplexobj(out)",
                "frequency = np.array([0.0, {{omega0}}, 2.0 * {{omega0}}]); out = {{fn}}(frequency, {{omega0}}, {{damping}}, {{drive}})\nassert np.all(np.isfinite(out.real))",
                "frequency = np.array([0.3, 0.9, 1.7]); out = {{fn}}(frequency, {{omega0}}, {{damping}}, {{drive}})\nassert np.allclose(out, target)",
            ), "return susceptibility", "The imaginary part represents dissipative response; the denominator must retain its sign and units."),
            _step("resonance", "Locate the peak absorption frequency and estimate the full-width at half-maximum from a sampled susceptibility curve. Reuse {{fn_susceptibility}}.", "def {{fn}}(frequency: np.ndarray, susceptibility: np.ndarray) -> tuple[float, float]:", (
                "frequency = np.linspace(0.1, 4.0, {{frequencies}}); response = {{fn_susceptibility}}(frequency, {{omega0}}, {{damping}}, {{drive}})\nout = {{fn}}(frequency, response)\nassert out[0] >= frequency.min() and out[1] >= 0.0",
                "frequency = np.array([1.0, 2.0, 3.0]); response = np.array([1j, 4j, 1j]); out = {{fn}}(frequency, response)\nassert np.allclose(out, target)",
                "frequency = np.linspace(0.2, 3.5, {{frequencies}}); response = {{fn_susceptibility}}(frequency, {{omega0}}, {{damping}}, {{drive}}); out = {{fn}}(frequency, response)\nassert np.all(np.isfinite(out))",
            ), "return peak_frequency, linewidth", "A sampled FWHM estimate must handle the half-maximum crossing on both sides of the peak."),
            _step("diagnostics", "Return the complex response, peak frequency, integrated absorption, and {{diagnostic_label}} for the supplied grid.", "def {{fn}}(frequency: np.ndarray, natural_frequency: float, damping: float, drive: float) -> tuple[np.ndarray, float, float, float]:", (
                "frequency = np.linspace(0.1, 4.0, {{frequencies}}); out = {{fn}}(frequency, {{omega0}}, {{damping}}, {{drive}})\nassert out[0].shape == frequency.shape and np.isfinite(out[1:]).all()",
                "frequency = np.array([0.5, 1.0, 1.5, 2.0]); out = {{fn}}(frequency, {{omega0}}, {{damping}}, {{drive}})\nassert np.allclose(np.asarray(out[1:]), np.asarray(target[1:]))",
                "frequency = np.linspace(0.2, 5.0, {{frequencies}}); out = {{fn}}(frequency, {{omega0}}, {{damping}}, {{drive}})\nassert np.all(np.isfinite(out[0].real))",
            ), "return susceptibility, peak_frequency, absorbed_area, diagnostic", "Integrated absorption and resonance location provide independent checks on a response implementation."),
        ),
        (_mode("analytic", "analytic frequency response", "Evaluate the closed-form complex response directly.", "The closed form avoids time-window leakage but requires a consistent Fourier sign convention."), _mode("ode", "time-domain integration", "Integrate the driven oscillator and estimate the response from the steady-state segment.", "Time-domain estimates require discarding transients and documenting the sampling interval.")),
        ("bounded", "noisy", "irregular", "calibrated"), ("spectrum", "uncertainty", "robustness"),
        "SciPy signal-processing reference routines", "https://github.com/scipy/scipy", ("response", "spectroscopy", "complex-valued"),
    ),
    _recipe(
        "ising_metropolis", "physics", "statistical-physics",
        "Finite-lattice Ising sampling and thermodynamic observables",
        "Implement a two-dimensional Ising Monte Carlo experiment, accept spin flips with the selected sampling rule, and estimate magnetization and heat-capacity-like fluctuations. {{scenario_description}} {{mode_description}}",
        "The square lattice has side {{size}}, inverse temperature beta={{beta}}, and {{sweeps}} sweeps. Spins are plus or minus one. Use seed={{seed}} only through a local random generator.",
        "import numpy as np",
        "The Ising model turns local energy differences into a global phase transition signal. Periodic neighbors, equilibration, and the distinction between per-spin and total observables must be explicit. {{scenario_background}} {{mode_background}}",
        _p_ising,
        (
            _step("energy", "Compute the nearest-neighbor energy and magnetization of a spin lattice without double-counting bonds.", "def {{fn}}(spins: np.ndarray, coupling: float = 1.0) -> tuple[float, float]:", (
                "spins = np.ones(({{size}}, {{size}}), dtype=int); out = {{fn}}(spins)\nassert np.allclose(out[1], 1.0)",
                "rng = np.random.default_rng({{seed}}); spins = rng.choice([-1, 1], size=({{size}}, {{size}})); out = {{fn}}(spins)\nassert np.isfinite(out[0]) and -1.0 <= out[1] <= 1.0",
                "spins = np.array([[1, -1], [-1, 1]]); out = {{fn}}(spins)\nassert np.allclose(out, target)",
            ), "return energy_per_spin, magnetization_per_spin", "Each bond contributes once to the Hamiltonian. Magnetization is normalized by the number of sites."),
            _step("sweep", "Perform one Monte Carlo sweep of single-spin proposals using the selected acceptance rule and return a new lattice plus the accepted count.", "def {{fn}}(spins: np.ndarray, beta: float, rng: np.random.Generator) -> tuple[np.ndarray, int]:", (
                "rng = np.random.default_rng({{seed}}); spins = np.ones(({{size}}, {{size}}), dtype=int); out = {{fn}}(spins, {{beta}}, rng)\nassert out[0].shape == spins.shape and 0 <= out[1] <= spins.size",
                "rng = np.random.default_rng({{seed}}); spins = -np.ones(({{size}}, {{size}}), dtype=int); before = spins.copy(); out = {{fn}}(spins, {{beta}}, rng)\nassert np.array_equal(spins, before)",
                "rng = np.random.default_rng(3); spins = rng.choice([-1, 1], size=({{size}}, {{size}})); out = {{fn}}(spins, {{beta}}, rng)\nassert np.all(np.isin(out[0], [-1, 1]))",
            ), "return spins_next, accepted", "The Metropolis ratio depends only on the local energy change. A sweep must use a reproducible local generator."),
            _step("observables", "Run equilibration and sampling sweeps and return mean energy, mean magnetization, susceptibility proxy, and {{diagnostic_label}}.", "def {{fn}}(spins0: np.ndarray, beta: float, sweeps: int, seed: int) -> tuple[float, float, float, float]:", (
                "spins0 = np.ones(({{size}}, {{size}}), dtype=int); out = {{fn}}(spins0, {{beta}}, 4, {{seed}})\nassert np.all(np.isfinite(out))",
                "spins0 = -np.ones(({{size}}, {{size}}), dtype=int); out = {{fn}}(spins0, {{beta}}, {{sweeps}}, {{seed}})\nassert np.allclose(out, target)",
                "rng = np.random.default_rng(8); spins0 = rng.choice([-1, 1], size=({{size}}, {{size}})); out = {{fn}}(spins0, {{beta}}, 5, {{seed}})\nassert out[2] >= 0.0",
            ), "return mean_energy, mean_magnetization, susceptibility, diagnostic", "Fluctuation observables depend on the sampling convention and normalization; report the convention through the function contract."),
        ),
        (_mode("metropolis", "Metropolis-Hastings", "Use the standard single-spin Metropolis acceptance probability.", "Metropolis sampling is simple but autocorrelated near the critical region."), _mode("heatbath", "heat-bath flips", "Use the conditional heat-bath probability for a proposed spin.", "Heat-bath updates sample the local conditional distribution directly.")),
        ("periodic", "stochastic", "ensemble", "bounded"), ("invariant", "uncertainty", "robustness"),
        "Open-source statistical-physics reference implementation family", "https://github.com/hoomd-blue/hoomd-blue", ("monte-carlo", "lattice-model", "sampling"),
    ),
    _recipe(
        "nbody_verlet", "physics", "astrodynamics",
        "Softened gravitational N-body trajectory",
        "Integrate a small gravitational N-body system with softened pair forces, then measure energy and angular-momentum drift. {{scenario_description}} {{mode_description}}",
        "There are {{count}} bodies with positions and velocities in two dimensions, step dt={{dt}}, softening length={{softening}}, and {{steps}} integration steps. Masses are positive and supplied separately.",
        "import numpy as np",
        "Pairwise forces are equal and opposite; a symmetric integrator should preserve momentum and approximately conserve energy. Softening removes singular collisions without changing the requested sign convention. {{scenario_background}} {{mode_background}}",
        _p_nbody,
        (
            _step("force", "Compute softened pairwise accelerations and potential energy for a set of planar bodies. Exclude self-interactions and preserve pair symmetry.", "def {{fn}}(positions: np.ndarray, masses: np.ndarray, softening: float) -> tuple[np.ndarray, float]:", (
                "positions = np.zeros(({{count}}, 2)); masses = np.ones({{count}}); out = {{fn}}(positions, masses, {{softening}})\nassert out[0].shape == positions.shape and np.allclose(out[0], 0.0)",
                "positions = np.arange({{count}} * 2, dtype=float).reshape({{count}}, 2); masses = np.linspace(1.0, 2.0, {{count}}); out = {{fn}}(positions, masses, {{softening}})\nassert np.all(np.isfinite(out[0]))",
                "positions = np.array([[0.0, 0.0], [1.0, 0.0]]); masses = np.array([1.0, 2.0]); out = {{fn}}(positions, masses, {{softening}})\nassert np.allclose(out, target)",
            ), "return acceleration, potential_energy", "The softened denominator is r squared plus epsilon squared. Pair forces must satisfy Newton's third law."),
            _step("advance", "Advance positions and velocities for one or more steps with the selected symplectic update. Reuse {{fn_force}} and return fresh arrays.", "def {{fn}}(positions: np.ndarray, velocities: np.ndarray, masses: np.ndarray, dt: float, softening: float, steps: int) -> tuple[np.ndarray, np.ndarray]:", (
                "positions = np.zeros(({{count}}, 2)); velocities = np.zeros(({{count}}, 2)); masses = np.ones({{count}}); out = {{fn}}(positions, velocities, masses, {{dt}}, {{softening}}, 1)\nassert np.allclose(out[0], positions)",
                "rng = np.random.default_rng(23); positions = rng.normal(size=({{count}}, 2)); velocities = rng.normal(size=({{count}}, 2)); masses = np.ones({{count}}); out = {{fn}}(positions, velocities, masses, {{dt}}, {{softening}}, {{steps}})\nassert np.all(np.isfinite(np.asarray(out)))",
                "positions = np.array([[0.0, 0.0], [1.0, 0.0]]); velocities = np.array([[0.0, 0.2], [0.0, -0.1]]); masses = np.array([1.0, 1.0]); out = {{fn}}(positions, velocities, masses, {{dt}}, {{softening}}, 2)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return positions_next, velocities_next", "Symplectic updates reduce secular energy drift; the exact ordering of half-kicks is part of the contract."),
            _step("diagnostics", "Return the final state, total energy, total angular momentum, and {{diagnostic_label}} after the requested trajectory.", "def {{fn}}(positions0: np.ndarray, velocities0: np.ndarray, masses: np.ndarray, dt: float, softening: float, steps: int) -> tuple[np.ndarray, np.ndarray, float, float]:", (
                "positions0 = np.zeros(({{count}}, 2)); velocities0 = np.zeros(({{count}}, 2)); masses = np.ones({{count}}); out = {{fn}}(positions0, velocities0, masses, {{dt}}, {{softening}}, 1)\nassert out[0].shape == positions0.shape and np.all(np.isfinite(out[2:]))",
                "rng = np.random.default_rng(29); positions0 = rng.normal(size=({{count}}, 2)); velocities0 = rng.normal(size=({{count}}, 2)); masses = np.linspace(1.0, 1.5, {{count}}); out = {{fn}}(positions0, velocities0, masses, {{dt}}, {{softening}}, {{steps}})\nassert np.allclose(np.asarray(out[2:]), np.asarray(target[2:]))",
                "positions0 = np.array([[0.0, 0.0], [1.0, 0.0]]); velocities0 = np.array([[0.0, 0.1], [0.0, -0.1]]); masses = np.ones(2); out = {{fn}}(positions0, velocities0, masses, {{dt}}, {{softening}}, 2)\nassert np.all(np.isfinite(np.asarray(out)))",
            ), "return positions_final, velocities_final, total_energy, diagnostic", "Energy and angular momentum are independent global checks on pairwise force signs and time integration."),
        ),
        (_mode("verlet", "velocity Verlet", "Use a velocity-Verlet kick-drift-kick update.", "Velocity Verlet is time reversible for a fixed force law."), _mode("yoshida", "fourth-order composition", "Compose symmetric second-order steps with the stated fourth-order coefficients.", "A symmetric composition can reduce phase error but requires careful coefficient ordering.")),
        ("bounded", "stochastic", "anisotropic", "periodic"), ("invariant", "stability", "sensitivity"),
        "OpenMM molecular simulation reference algorithms", "https://github.com/openmm/openmm", ("n-body", "symplectic", "force-law"),
    ),
    _recipe(
        "reaction_rk4", "chemistry", "reaction-kinetics",
        "Reversible reaction network with a loss channel",
        "Model A+B <-> C with first-order loss, integrate the mass-action system, and report a stoichiometric residual over the trajectory. {{scenario_description}} {{mode_description}}",
        "Concentrations are nonnegative molar quantities. Use k_forward={{kf}}, k_reverse={{kr}}, k_loss={{kloss}}, dt={{dt}}, and {{steps}} integration steps.",
        "import numpy as np",
        "Mass action couples nonlinear fluxes through a stoichiometric matrix. Positivity and the declared loss channel must not be hidden by an arbitrary clipping rule. {{scenario_background}} {{mode_background}}",
        _p_reaction,
        (
            _step("rhs", "Evaluate the mass-action derivative for concentrations [A, B, C]. Return a length-three vector without mutating the input.", "def {{fn}}(concentrations: np.ndarray, k_forward: float, k_reverse: float, k_loss: float) -> np.ndarray:", (
                "c = np.array([1.2, 0.8, 0.3]); out = {{fn}}(c, {{kf}}, {{kr}}, {{kloss}})\nassert out.shape == (3,)",
                "c = np.zeros(3); out = {{fn}}(c, {{kf}}, {{kr}}, {{kloss}})\nassert np.allclose(out, 0.0)",
                "c = np.array([0.2, 1.5, 0.4]); out = {{fn}}(c, {{kf}} * 0.7, {{kr}} * 1.2, {{kloss}} * 0.5)\nassert np.allclose(out, target)",
            ), "return derivative", "The reversible flux is kf*A*B-kr*C. Its signs follow the reaction stoichiometry, while the loss acts only on C."),
            _step("rk4", "Advance one classical fourth-order Runge-Kutta step by calling {{fn_rhs}} at all four stages. Return a fresh concentration vector.", "def {{fn}}(concentrations: np.ndarray, dt: float, k_forward: float, k_reverse: float, k_loss: float) -> np.ndarray:", (
                "c = np.array([1.0, 0.5, 0.1]); out = {{fn}}(c, {{dt}}, {{kf}}, {{kr}}, {{kloss}})\nassert out.shape == c.shape",
                "c = np.zeros(3); out = {{fn}}(c, {{dt}}, {{kf}}, {{kr}}, {{kloss}})\nassert np.allclose(out, c)",
                "c = np.array([0.7, 0.9, 0.2]); before = c.copy(); out = {{fn}}(c, {{dt}}, {{kf}}, {{kr}}, {{kloss}})\nassert np.array_equal(c, before)",
                "c = np.array([0.3, 1.1, 0.4]); out = {{fn}}(c, {{dt}}, {{kf}}, {{kr}}, {{kloss}})\nassert np.allclose(out, target)",
            ), "return concentrations_next", "RK4 evaluates nonlinear midpoint states; reusing a single derivative would reduce the method to a lower order."),
            _step("trajectory", "Integrate the network for the requested steps and return the trajectory, final state, and maximum moiety-balance residual.", "def {{fn}}(initial: np.ndarray, dt: float, steps: int, k_forward: float, k_reverse: float, k_loss: float) -> tuple[np.ndarray, np.ndarray, float]:", (
                "initial = np.array([1.0, 0.8, 0.0]); out = {{fn}}(initial, {{dt}}, 2, {{kf}}, {{kr}}, {{kloss}})\nassert out[0].shape[1] == 3 and out[1].shape == (3,)",
                "initial = np.array([0.0, 0.0, 0.0]); out = {{fn}}(initial, {{dt}}, {{steps}}, {{kf}}, {{kr}}, {{kloss}})\nassert np.allclose(out[1], 0.0)",
                "initial = np.array([0.5, 0.4, 0.1]); out = {{fn}}(initial, {{dt}}, {{steps}}, {{kf}}, {{kr}}, {{kloss}})\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return trajectory, final_concentrations, max_residual", "The trajectory is part of the observable. A balance residual should be computed before the explicit loss term is removed."),
        ),
        (_mode("rk4", "classical RK4", "Use four derivative evaluations with classical one-half and one-sixth weights.", "RK4 is accurate for smooth nonstiff kinetics but can require a small step for separated rates."), _mode("rosenbrock", "linearly implicit update", "Use a linearly implicit stage solve for the stiff rate scale.", "A linearly implicit method must document how the Jacobian is evaluated.")),
        ("positive", "stiff", "ensemble", "noisy"), ("invariant", "stability", "sensitivity"),
        "SciPy integration and chemical-kinetics reference formulations", "https://github.com/scipy/scipy", ("kinetics", "mass-action", "ode"),
    ),
    _recipe(
        "enzyme_fit", "chemistry", "biochemistry",
        "Enzyme saturation curve and weighted parameter fit",
        "Construct a Michaelis-Menten rate curve, fit its kinetic parameters under known observation uncertainty, and return a residual and local covariance summary. {{scenario_description}} {{mode_description}}",
        "Use {{points}} substrate concentrations, reference Vmax={{vmax}}, Km={{km}}, and observation noise scale {{noise}}. Concentrations are positive and rates use consistent units.",
        "import numpy as np\nfrom scipy.optimize import least_squares",
        "The saturation curve is nonlinear in substrate and its reciprocal transform distorts noise. The fit must use the stated weights and expose parameter identifiability. {{scenario_background}} {{mode_background}}",
        _p_enzyme,
        (
            _step("rate", "Evaluate the Michaelis-Menten rate v=Vmax*S/(Km+S) for a vector of substrate concentrations and preserve broadcasting.", "def {{fn}}(substrate: np.ndarray, vmax: float, km: float) -> np.ndarray:", (
                "s = np.linspace(0.0, 4.0, {{points}}); out = {{fn}}(s, {{vmax}}, {{km}})\nassert out.shape == s.shape and np.all(out >= 0.0)",
                "s = np.array([0.0, {{km}}, 100.0 * {{km}}]); out = {{fn}}(s, {{vmax}}, {{km}})\nassert np.isclose(out[0], 0.0)",
                "s = np.array([0.2, 0.7, 2.4]); out = {{fn}}(s, {{vmax}}, {{km}})\nassert np.allclose(out, target)",
            ), "return rate", "At low substrate the response is approximately linear; at high substrate it approaches Vmax without exceeding it."),
            _step("fit", "Fit Vmax and Km by weighted nonlinear least squares. Reuse {{fn_rate}}, keep both parameters positive, and return the fitted vector.", "def {{fn}}(substrate: np.ndarray, observations: np.ndarray, sigma: np.ndarray) -> np.ndarray:", (
                "s = np.linspace(0.1, 3.0, {{points}}); y = {{fn_rate}}(s, {{vmax}}, {{km}}); out = {{fn}}(s, y, np.full(s.size, {{noise}}))\nassert out.shape == (2,) and np.all(out > 0.0)",
                "s = np.array([0.1, 1.0, 4.0]); y = np.array([0.08, 0.5, 1.2]); out = {{fn}}(s, y, np.array([0.1, 0.2, 0.4]))\nassert np.all(np.isfinite(out))",
                "s = np.linspace(0.2, 2.5, {{points}}); y = {{fn_rate}}(s, {{vmax}}, {{km}}) + {{noise}} * np.sin(s); out = {{fn}}(s, y, np.full(s.size, {{noise}}))\nassert np.allclose(out, target)",
            ), "return fitted_vmax, fitted_km", "Weighted residuals prevent high-variance points from dominating the kinetic estimate; positivity is enforced through the parameterization."),
            _step("diagnostics", "Return fitted parameters, weighted RMS residual, and a covariance or sensitivity scalar for the experiment.", "def {{fn}}(substrate: np.ndarray, observations: np.ndarray, sigma: np.ndarray) -> tuple[np.ndarray, float, float]:", (
                "s = np.linspace(0.1, 3.0, {{points}}); y = {{fn_rate}}(s, {{vmax}}, {{km}}); out = {{fn}}(s, y, np.full(s.size, {{noise}}))\nassert out[0].shape == (2,) and np.all(np.isfinite(out))",
                "s = np.array([0.2, 0.5, 1.5, 3.0]); y = np.array([0.1, 0.3, 0.8, 1.1]); out = {{fn}}(s, y, np.ones(4) * 0.1)\nassert np.allclose(np.asarray(out), np.asarray(target))",
                "s = np.linspace(0.2, 2.5, {{points}}); y = {{fn_rate}}(s, {{vmax}}, {{km}}); out = {{fn}}(s, y, np.full(s.size, {{noise}}))\nassert out[1] >= 0.0 and out[2] >= 0.0",
            ), "return fitted_parameters, weighted_rms, diagnostic", "A fit can look plausible while being poorly identified; the final scalar makes conditioning or uncertainty observable."),
        ),
        (_mode("trust_region", "trust-region least squares", "Use a bounded trust-region least-squares solve.", "Trust-region steps control excursions when Vmax and Km are correlated."), _mode("log_parameter", "log-parameter optimization", "Optimize logarithms of the positive parameters and transform back.", "Log coordinates encode positivity while changing the local metric.")),
        ("noisy", "positive", "irregular", "bounded"), ("uncertainty", "likelihood", "sensitivity"),
        "SciPy optimize reference API", "https://github.com/scipy/scipy", ("biochemistry", "nonlinear-fit", "uncertainty"),
    ),
    _recipe(
        "acid_speciation", "chemistry", "physical-chemistry",
        "Diprotic acid speciation from charge balance",
        "Compute species fractions for a diprotic acid across pH, solve a charge-balance equation for an unknown pH, and report a titration sensitivity. {{scenario_description}} {{mode_description}}",
        "Use pKa1={{pka1}}, pKa2={{pka2}}, total analytical concentration={{total}}, and a pH grid of length {{grid}}. Fractions must sum to one within numerical tolerance.",
        "import numpy as np\nfrom scipy.optimize import brentq",
        "Acid-base speciation is governed by mass balance and charge neutrality. Fractions change over several orders of magnitude, so stable exponentials and a clear pH convention are required. {{scenario_background}} {{mode_background}}",
        _p_acid,
        (
            _step("fractions", "Evaluate the four diprotic species fractions at each pH using the alpha denominator and return an array whose final axis indexes H2A, HA-, A2-, and OH-related charge contribution.", "def {{fn}}(pH: np.ndarray, pka1: float, pka2: float) -> np.ndarray:", (
                "pH = np.linspace(0.0, 14.0, {{grid}}); out = {{fn}}(pH, {{pka1}}, {{pka2}})\nassert out.shape == ({{grid}}, 3)",
                "pH = np.array([{{pka1}}, {{pka2}}]); out = {{fn}}(pH, {{pka1}}, {{pka2}})\nassert np.all(np.isfinite(out))",
                "pH = np.array([2.0, 7.0, 12.0]); out = {{fn}}(pH, {{pka1}}, {{pka2}})\nassert np.allclose(out, target)",
            ), "return alpha_species", "The denominator is 1+Ka1/H+Ka1*Ka2/H squared. Working in log space avoids loss of significance at extreme pH."),
            _step("balance", "Solve the electroneutrality equation for pH given a strong-ion difference and total acid concentration. Reuse {{fn_fractions}} and return the root.", "def {{fn}}(strong_ion_difference: float, total_concentration: float, pka1: float, pka2: float) -> float:", (
                "out = {{fn}}(0.0, {{total}}, {{pka1}}, {{pka2}})\nassert 0.0 <= out <= 14.0",
                "out = {{fn}}(0.001, {{total}} * 0.5, {{pka1}}, {{pka2}})\nassert np.isfinite(out)",
                "out = {{fn}}(-0.0005, {{total}} * 1.2, {{pka1}}, {{pka2}})\nassert np.allclose(out, target)",
            ), "return pH_root", "A bracketed root solve is preferable to an unconstrained Newton step when the charge curve has a flat region."),
            _step("diagnostics", "Return the fraction table, charge residual at a supplied pH, and the local pH sensitivity together with {{diagnostic_label}}.", "def {{fn}}(pH: np.ndarray, total_concentration: float, pka1: float, pka2: float) -> tuple[np.ndarray, float, float, float]:", (
                "pH = np.linspace(0.0, 14.0, {{grid}}); out = {{fn}}(pH, {{total}}, {{pka1}}, {{pka2}})\nassert out[0].shape == ({{grid}}, 3) and np.all(np.isfinite(out[1:]))",
                "pH = np.array([1.0, 7.0, 13.0]); out = {{fn}}(pH, {{total}}, {{pka1}}, {{pka2}})\nassert np.allclose(np.asarray(out[1:]), np.asarray(target[1:]))",
                "pH = np.linspace(2.0, 12.0, 5); out = {{fn}}(pH, {{total}}, {{pka1}}, {{pka2}})\nassert np.all(np.isfinite(np.asarray(out)))",
            ), "return alpha_species, charge_residual, sensitivity, diagnostic", "The derivative of the charge curve identifies buffering regions and catches a transposed species axis."),
        ),
        (_mode("logsum", "log-domain alpha evaluation", "Evaluate alpha fractions from log concentrations and normalize with log-sum-exp.", "The log-domain form is stable when pH is far from both pKa values."), _mode("direct", "direct equilibrium ratios", "Evaluate equilibrium ratios directly and normalize with a guarded denominator.", "Direct ratios are simpler but require explicit protection against overflow.")),
        ("positive", "bounded", "calibrated", "irregular"), ("invariant", "sensitivity", "stability"),
        "SciPy root-finding reference API", "https://github.com/scipy/scipy", ("speciation", "equilibrium", "root-solving"),
    ),
    _recipe(
        "partition_thermo", "chemistry", "statistical-thermodynamics",
        "Canonical partition function and fluctuation thermodynamics",
        "Evaluate a discrete canonical partition function, derive mean energy and heat capacity, and compare thermodynamic observables across temperature. {{scenario_description}} {{mode_description}}",
        "Use {{levels}} energy levels separated by {{spacing}}, reference temperature {{temperature}}, and dimensionless beta=1/T. Energies and kB are dimensionless for this exercise.",
        "import numpy as np\nfrom scipy.special import logsumexp",
        "The partition sum is dominated by the lowest levels at low temperature and by degeneracy at high temperature. Log-sum-exp prevents underflow while preserving fluctuation identities. {{scenario_background}} {{mode_background}}",
        _p_partition,
        (
            _step("logweights", "Compute normalized Boltzmann weights and log partition function for a supplied energy array using a stable normalization.", "def {{fn}}(energies: np.ndarray, temperature: float) -> tuple[np.ndarray, float]:", (
                "energies = np.arange({{levels}}, dtype=float) * {{spacing}}; out = {{fn}}(energies, {{temperature}})\nassert out[0].shape == energies.shape and np.isclose(out[0].sum(), 1.0)",
                "energies = np.array([0.0, 100.0, 200.0]); out = {{fn}}(energies, 0.5)\nassert np.all(np.isfinite(out[0]))",
                "energies = np.array([-1.0, 0.0, 2.0]); out = {{fn}}(energies, 1.3)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return probabilities, log_partition", "Subtracting the maximum log weight before exponentiation keeps the probability vector normalized at extreme temperatures."),
            _step("observables", "Derive mean energy, energy variance, entropy, and heat capacity from the weights. Reuse {{fn_logweights}} and return scalar observables.", "def {{fn}}(energies: np.ndarray, temperature: float) -> tuple[float, float, float, float]:", (
                "energies = np.arange({{levels}}, dtype=float) * {{spacing}}; out = {{fn}}(energies, {{temperature}})\nassert np.all(np.isfinite(out)) and out[1] >= 0.0",
                "energies = np.array([0.0, 1.0]); out = {{fn}}(energies, 0.8)\nassert np.allclose(np.asarray(out), np.asarray(target))",
                "energies = np.array([0.0, 0.2, 0.9, 1.8]); out = {{fn}}(energies, 2.0)\nassert out[2] >= 0.0",
            ), "return mean_energy, variance, entropy, heat_capacity", "Heat capacity is beta squared times energy variance in dimensionless units; mixing beta and temperature produces a detectable scale error."),
            _step("diagnostics", "Evaluate observables on a temperature grid and return the grid, heat-capacity peak, and {{diagnostic_label}}.", "def {{fn}}(energies: np.ndarray, temperatures: np.ndarray) -> tuple[np.ndarray, float, float]:", (
                "energies = np.arange({{levels}}, dtype=float) * {{spacing}}; temperatures = np.linspace(0.3, 3.0, 8); out = {{fn}}(energies, temperatures)\nassert out[0].shape == temperatures.shape",
                "energies = np.array([0.0, 1.0, 2.0]); temperatures = np.array([0.5, 1.0, 2.0]); out = {{fn}}(energies, temperatures)\nassert np.allclose(np.asarray(out[1:]), np.asarray(target[1:]))",
                "energies = np.array([0.0, 0.1]); temperatures = np.array([0.2, 0.4, 0.8]); out = {{fn}}(energies, temperatures)\nassert np.all(np.isfinite(np.asarray(out)))",
            ), "return heat_capacity_curve, peak_temperature, diagnostic", "A peak location and a fluctuation diagnostic test both the temperature axis and the normalization of the partition sum."),
        ),
        (_mode("logsumexp", "log-sum-exp evaluation", "Use a log-sum-exp reduction for all partition sums.", "Log-sum-exp preserves relative weights when beta times energy is large."), _mode("scaled", "scaled reference energy", "Subtract a reference energy before evaluating and add it back to log Z.", "Thermodynamic derivatives are invariant to an energy offset if the offset is restored consistently.")),
        ("positive", "bounded", "ensemble", "calibrated"), ("stability", "spectrum", "sensitivity"),
        "SciPy special-function reference API", "https://github.com/scipy/scipy", ("partition-function", "thermodynamics", "fluctuation"),
    ),
    _recipe(
        "crystal_powder", "materials", "diffraction",
        "Crystal structure factor and powder diffraction profile",
        "Construct a reciprocal-lattice structure factor for a finite basis, broaden the allowed reflections into a powder profile, and report peak positions and integrated intensity. {{scenario_description}} {{mode_description}}",
        "The basis contains {{atoms}} fractional coordinates, the profile has {{bins}} two-theta bins, wavelength={{wavelength}}, and Gaussian width={{width}}. Atomic form factors are supplied as real weights.",
        "import numpy as np",
        "Diffraction intensity is the squared magnitude of a coherent complex sum. Fractional-coordinate conventions, multiplicity, and profile normalization must be stated rather than inferred from a plot. {{scenario_background}} {{mode_background}}",
        _p_crystal,
        (
            _step("structure_factor", "Evaluate complex structure factors for a list of reciprocal integer triplets and a fractional-coordinate basis. Preserve the ordering of reflections.", "def {{fn}}(hkl: np.ndarray, fractional_positions: np.ndarray, weights: np.ndarray) -> np.ndarray:", (
                "hkl = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0]]); positions = np.zeros(({{atoms}}, 3)); weights = np.ones({{atoms}}); out = {{fn}}(hkl, positions, weights)\nassert out.shape == (3,) and np.iscomplexobj(out)",
                "hkl = np.array([[1, 0, 0], [0, 1, 0]]); positions = np.array([[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]]); weights = np.array([1.0, 2.0]); out = {{fn}}(hkl, positions, weights)\nassert np.all(np.isfinite(out.real))",
                "hkl = np.array([[1, 2, 0], [2, 1, 1]]); positions = np.zeros((2, 3)); weights = np.array([1.0, 0.7]); out = {{fn}}(hkl, positions, weights)\nassert np.allclose(out, target)",
            ), "return structure_factors", "The phase is 2*pi*i*(h*x+k*y+l*z). Intensities are formed only after summing amplitudes over the basis."),
            _step("profile", "Map nonzero reflections to two-theta using Bragg's law and accumulate a normalized Gaussian powder profile. Reuse {{fn_structure_factor}}.", "def {{fn}}(hkl: np.ndarray, lattice_spacing: np.ndarray, fractional_positions: np.ndarray, weights: np.ndarray, wavelength: float, two_theta: np.ndarray, width: float) -> np.ndarray:", (
                "hkl = np.array([[1, 0, 0], [1, 1, 0]]); d = np.array([2.0, 1.4]); positions = np.zeros(({{atoms}}, 3)); weights = np.ones({{atoms}}); grid = np.linspace(0.1, 150.0, {{bins}}); out = {{fn}}(hkl, d, positions, weights, {{wavelength}}, grid, {{width}})\nassert out.shape == grid.shape and np.all(out >= 0.0)",
                "hkl = np.array([[1, 0, 0]]); d = np.array([1.0]); positions = np.array([[0.0, 0.0, 0.0]]); weights = np.array([1.0]); grid = np.array([30.0, 60.0, 90.0]); out = {{fn}}(hkl, d, positions, weights, 1.0, grid, {{width}})\nassert np.all(np.isfinite(out))",
                "hkl = np.array([[1, 0, 0], [2, 0, 0]]); d = np.array([2.0, 1.0]); positions = np.zeros((2, 3)); weights = np.ones(2); grid = np.linspace(1.0, 120.0, 12); out = {{fn}}(hkl, d, positions, weights, {{wavelength}}, grid, {{width}})\nassert np.allclose(out, target)",
            ), "return intensity_profile", "Bragg's angle is defined only when wavelength/(2d) is in [0,1]. A profile is normalized after summing reflection intensities."),
            _step("peaks", "Extract the strongest reflection positions, integrated profile area, and {{diagnostic_label}} from the calculated powder profile.", "def {{fn}}(two_theta: np.ndarray, intensity: np.ndarray, peak_count: int) -> tuple[np.ndarray, float, float]:", (
                "grid = np.linspace(0.1, 150.0, {{bins}}); intensity = np.exp(-0.5 * ((grid - 40.0) / {{width}}) ** 2); out = {{fn}}(grid, intensity, 3)\nassert out[0].ndim == 1 and out[1] >= 0.0",
                "grid = np.array([10.0, 20.0, 30.0]); intensity = np.array([0.1, 0.8, 0.2]); out = {{fn}}(grid, intensity, 1)\nassert np.allclose(np.asarray(out), np.asarray(target))",
                "grid = np.linspace(1.0, 120.0, 20); intensity = np.ones(20); out = {{fn}}(grid, intensity, 4)\nassert np.all(np.isfinite(np.asarray(out)))",
            ), "return peak_positions, integrated_area, diagnostic", "Peak extraction must define ties, edge bins, and whether area uses the trapezoidal grid spacing."),
        ),
        (_mode("kinematic", "kinematic Bragg mapping", "Use the analytic d-spacing and Bragg-angle mapping supplied in the contract.", "Kinematic diffraction ignores multiple scattering but preserves the coherent basis sum."), _mode("reciprocal", "reciprocal-vector mapping", "Compute reciprocal vectors first and derive d-spacing from their norm.", "The reciprocal-vector convention fixes the factor of 2*pi and the lattice metric.")),
        ("bounded", "anisotropic", "calibrated", "irregular"), ("spectrum", "robustness", "calibration"),
        "pymatgen crystal-structure reference project", "https://github.com/materialsproject/pymatgen", ("materials", "diffraction", "complex-sum"),
    ),
    _recipe(
        "phonon_modes", "materials", "lattice-dynamics",
        "Mass-weighted phonon modes on a one-dimensional chain",
        "Assemble a periodic chain dynamical matrix, diagonalize its mass-weighted modes, and compute a density-of-states summary. {{scenario_description}} {{mode_description}}",
        "Use {{sites}} sites per unit cell, spring coupling={{coupling}}, mass={{mass}}, and {{qpoints}} wave-vector samples. Frequencies are returned in ascending order.",
        "import numpy as np\nfrom scipy.linalg import eigh",
        "Phonon frequencies are square roots of eigenvalues of a mass-weighted dynamical matrix. Translational zero modes and eigenvector normalization are scientific invariants. {{scenario_background}} {{mode_background}}",
        _p_phonon,
        (
            _step("dynamical", "Build the Hermitian dynamical matrix at a supplied reduced wave vector for a periodic nearest-neighbor chain.", "def {{fn}}(wave_vector: float, site_count: int, spring: float, mass: float) -> np.ndarray:", (
                "out = {{fn}}(0.0, {{sites}}, {{coupling}}, {{mass}})\nassert out.shape == ({{sites}}, {{sites}}) and np.allclose(out, out.T.conj())",
                "out = {{fn}}(np.pi, {{sites}}, {{coupling}}, {{mass}})\nassert np.all(np.linalg.eigvalsh(out) >= -1e-10)",
                "out = {{fn}}(0.37, 3, 1.2, 0.8)\nassert np.allclose(out, target)",
            ), "return dynamical_matrix", "Mass weighting divides force constants by the square root of the endpoint masses; Hermiticity is required for real frequencies."),
            _step("modes", "Diagonalize the dynamical matrix over a wave-vector grid and return positive frequencies and mass-normalized eigenvectors. Reuse {{fn_dynamical}}.", "def {{fn}}(wave_vectors: np.ndarray, site_count: int, spring: float, mass: float) -> tuple[np.ndarray, np.ndarray]:", (
                "q = np.linspace(0.0, np.pi, {{qpoints}}); out = {{fn}}(q, {{sites}}, {{coupling}}, {{mass}})\nassert out[0].shape == ({{qpoints}}, {{sites}})",
                "q = np.array([0.0, np.pi]); out = {{fn}}(q, 2, 1.0, 1.0)\nassert np.all(np.isfinite(out[0]))",
                "q = np.array([0.2, 0.7, 1.1]); out = {{fn}}(q, 3, 0.9, 1.3)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return frequencies, eigenvectors", "The acoustic branch approaches zero at q=0. Eigenvectors should be normalized under the mass metric, not an arbitrary Euclidean scaling."),
            _step("dos", "Aggregate the mode frequencies into a normalized density-of-states histogram and return its centroid and {{diagnostic_label}}.", "def {{fn}}(frequencies: np.ndarray, bin_edges: np.ndarray) -> tuple[np.ndarray, float, float]:", (
                "q = np.linspace(0.0, np.pi, {{qpoints}}); frequencies, _ = {{fn_modes}}(q, {{sites}}, {{coupling}}, {{mass}}); edges = np.linspace(0.0, 4.0, 16); out = {{fn}}(frequencies, edges)\nassert out[0].shape == (15,) and np.all(out[0] >= 0.0)",
                "frequencies = np.array([[0.0, 1.0], [0.5, 1.5]]); edges = np.array([0.0, 1.0, 2.0]); out = {{fn}}(frequencies, edges)\nassert np.allclose(np.asarray(out), np.asarray(target))",
                "frequencies = np.ones((3, {{sites}})); edges = np.linspace(0.0, 2.0, 8); out = {{fn}}(frequencies, edges)\nassert np.all(np.isfinite(np.asarray(out)))",
            ), "return density, centroid, diagnostic", "Histogram normalization must account for the number of q points and modes; bin-edge conventions affect the centroid."),
        ),
        (_mode("finite_difference", "finite-difference force constants", "Assemble nearest-neighbor force constants by finite differences of the pair potential.", "The finite-difference convention determines the sign of off-diagonal force constants."), _mode("analytic", "analytic force constants", "Use the analytic spring Hessian and impose periodic wraparound.", "The analytic Hessian makes the acoustic sum rule explicit.")),
        ("periodic", "anisotropic", "bounded", "multichannel"), ("spectrum", "invariant", "stability"),
        "ASE atomistic simulation reference project", "https://github.com/DeepChoudhury/ase", ("materials", "phonon", "eigenproblem"),
    ),
    _recipe(
        "cahn_hilliard", "materials", "phase-field",
        "Cahn-Hilliard phase separation with spectral updates",
        "Advance a conserved phase field with a spectral Cahn-Hilliard update, compute free energy, and quantify domain coarsening. {{scenario_description}} {{mode_description}}",
        "The phase field is a square array of side {{n}}, interface scale epsilon={{epsilon}}, mobility={{mobility}}, and time step={{dt}}. The mean composition must be conserved.",
        "import numpy as np\nfrom numpy.fft import fft2, ifft2, fftfreq",
        "Cahn-Hilliard dynamics is a fourth-order conserved gradient flow. A correct spectral update preserves mean composition while decreasing the discrete free energy. {{scenario_background}} {{mode_background}}",
        _p_phase,
        (
            _step("chemical_potential", "Compute the double-well chemical potential and its Laplacian for a periodic phase field.", "def {{fn}}(phase: np.ndarray, epsilon: float) -> tuple[np.ndarray, np.ndarray]:", (
                "phase = np.zeros(({{n}}, {{n}})); phase[{{n}} // 2:, :] = 1.0; out = {{fn}}(phase, {{epsilon}})\nassert out[0].shape == phase.shape and out[1].shape == phase.shape",
                "phase = np.full(({{n}}, {{n}}), 0.5); out = {{fn}}(phase, {{epsilon}})\nassert np.all(np.isfinite(np.asarray(out)))",
                "rng = np.random.default_rng(31); phase = rng.uniform(0.2, 0.8, size=({{n}}, {{n}})); out = {{fn}}(phase, {{epsilon}})\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return chemical_potential, laplacian", "The derivative of the quartic free-energy density is phi cubed minus phi. The gradient term contributes a Laplacian."),
            _step("advance", "Advance the conserved phase field for one spectral time step using the selected stabilization and return a fresh array.", "def {{fn}}(phase: np.ndarray, epsilon: float, mobility: float, dt: float) -> np.ndarray:", (
                "phase = np.full(({{n}}, {{n}}), 0.5); out = {{fn}}(phase, {{epsilon}}, {{mobility}}, {{dt}})\nassert out.shape == phase.shape and np.isclose(out.mean(), phase.mean(), atol=1e-8)",
                "rng = np.random.default_rng(37); phase = rng.uniform(0.3, 0.7, size=({{n}}, {{n}})); out = {{fn}}(phase, {{epsilon}}, {{mobility}}, {{dt}})\nassert np.all(np.isfinite(out))",
                "phase = np.zeros(({{n}}, {{n}})); phase[::2, ::2] = 1.0; out = {{fn}}(phase, {{epsilon}}, {{mobility}}, {{dt}})\nassert np.allclose(out, target)",
            ), "return phase_next", "The Fourier multiplier contains a squared wave number; stabilization changes the high-frequency denominator but not the conserved zero mode."),
            _step("diagnostics", "Run the phase-field update and return the final field, mean composition, free energy, and {{diagnostic_label}}.", "def {{fn}}(phase0: np.ndarray, epsilon: float, mobility: float, dt: float, steps: int) -> tuple[np.ndarray, float, float, float]:", (
                "phase0 = np.full(({{n}}, {{n}}), 0.5); out = {{fn}}(phase0, {{epsilon}}, {{mobility}}, {{dt}}, 2)\nassert out[0].shape == phase0.shape and np.isfinite(out[1:]).all()",
                "phase0 = np.zeros(({{n}}, {{n}})); phase0[{{n}} // 2:] = 1.0; out = {{fn}}(phase0, {{epsilon}}, {{mobility}}, {{dt}}, 4)\nassert np.allclose(np.asarray(out[1:]), np.asarray(target[1:]))",
                "rng = np.random.default_rng(41); phase0 = rng.uniform(0.2, 0.8, size=({{n}}, {{n}})); out = {{fn}}(phase0, {{epsilon}}, {{mobility}}, {{dt}}, 3)\nassert np.all(np.isfinite(np.asarray(out)))",
            ), "return phase_final, mean_composition, free_energy, diagnostic", "Free energy and mean composition provide independent checks on the variational derivative and the conserved update."),
        ),
        (_mode("semi_implicit", "semi-implicit spectral", "Treat the linear fourth-order term implicitly and the nonlinear term explicitly.", "Semi-implicit updates permit larger steps while retaining the conserved zero mode."), _mode("convex_split", "convex splitting", "Split the free energy into convex and concave parts before the spectral solve.", "Convex splitting provides a discrete energy-stability argument at the cost of an extra linear solve.")),
        ("periodic", "anisotropic", "stochastic", "bounded"), ("invariant", "stability", "sensitivity"),
        "FiPy phase-field reference project", "https://github.com/usnistgov/fipy", ("materials", "phase-field", "spectral"),
    ),
    _recipe(
        "percolation", "materials", "statistical-materials",
        "Bond percolation clusters and finite-size threshold",
        "Generate bond-occupation realizations on a square lattice, label connected clusters, and estimate a spanning probability and finite-size uncertainty. {{scenario_description}} {{mode_description}}",
        "Use a lattice side {{size}}, occupation probability {{probability}}, random seed={{seed}}, and {{trials}} independent trials. Neighbor bonds use the stated periodic or open convention.",
        "import numpy as np",
        "Percolation is a geometric phase transition. Union-find connectivity and a clearly defined spanning criterion are more important than a visually plausible cluster plot. {{scenario_background}} {{mode_background}}",
        _p_percolation,
        (
            _step("clusters", "Label connected occupied sites or bonds with a disjoint-set algorithm and return cluster labels and sizes.", "def {{fn}}(occupied: np.ndarray, periodic: bool) -> tuple[np.ndarray, np.ndarray]:", (
                "occupied = np.ones(({{size}}, {{size}}), dtype=bool); out = {{fn}}(occupied, True)\nassert out[0].shape == occupied.shape and out[1].size == 1",
                "occupied = np.zeros(({{size}}, {{size}}), dtype=bool); out = {{fn}}(occupied, False)\nassert out[1].size == 0",
                "occupied = np.zeros((4, 4), dtype=bool); occupied[0, 0] = occupied[0, 1] = True; out = {{fn}}(occupied, False)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return labels, cluster_sizes", "Union-find path compression changes runtime but not connectivity. Periodic wrapping must be applied exactly once per edge."),
            _step("spanning", "Determine whether a labeled realization spans the lattice in either direction and return the largest-cluster fraction.", "def {{fn}}(labels: np.ndarray, cluster_sizes: np.ndarray) -> tuple[bool, float]:", (
                "labels = np.arange({{size}} * {{size}}).reshape({{size}}, {{size}}); sizes = np.ones({{size}} * {{size}}); out = {{fn}}(labels, sizes)\nassert isinstance(out[0], (bool, np.bool_)) and 0.0 <= out[1] <= 1.0",
                "labels = np.zeros((3, 3), dtype=int); sizes = np.array([9]); out = {{fn}}(labels, sizes)\nassert out[0] is True",
                "labels = np.array([[0, 1], [0, 1]]); sizes = np.array([2, 2]); out = {{fn}}(labels, sizes)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return spans, largest_fraction", "A spanning cluster touches opposite boundaries; diagonal contact does not count for four-neighbor connectivity."),
            _step("estimate", "Run independent realizations over the requested trials and return spanning probability, mean largest-cluster fraction, and {{diagnostic_label}}.", "def {{fn}}(size: int, probability: float, trials: int, seed: int, periodic: bool) -> tuple[float, float, float]:", (
                "out = {{fn}}({{size}}, {{probability}}, 3, {{seed}}, False)\nassert np.all(np.isfinite(out)) and 0.0 <= out[0] <= 1.0",
                "out = {{fn}}(4, 1.0, 2, 7, False)\nassert np.isclose(out[0], 1.0)",
                "out = {{fn}}(5, 0.0, 2, 7, True)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return spanning_probability, mean_largest_fraction, diagnostic", "Finite-size uncertainty must be computed over realizations, not inferred from a single cluster."),
        ),
        (_mode("union_find", "union-find labeling", "Use disjoint-set unions while scanning each occupied bond once.", "Union-find makes connectivity explicit and scales to many trials."), _mode("flood_fill", "iterative flood fill", "Use an iterative breadth-first or depth-first flood fill with an explicit visited mask.", "An iterative traversal avoids recursion limits on large clusters.")),
        ("stochastic", "periodic", "bounded", "ensemble"), ("uncertainty", "robustness", "invariant"),
        "NetworkX graph-algorithm reference project", "https://github.com/networkx/networkx", ("materials", "percolation", "connectivity"),
    ),
    _recipe(
        "lotka_volterra", "biology", "population-dynamics",
        "Predator-prey dynamics and cycle diagnostics",
        "Integrate a predator-prey model, locate its positive equilibrium, and quantify the phase-space cycle from a simulated trajectory. {{scenario_description}} {{mode_description}}",
        "Use rates alpha={{alpha}}, beta={{beta}}, delta={{delta}}, gamma={{gamma}}, time step={{dt}}, and positive initial populations. Return trajectories with time along the first axis.",
        "import numpy as np\nfrom scipy.integrate import solve_ivp",
        "The Lotka-Volterra equations couple two populations through bilinear encounters. Positivity, equilibrium location, and cycle orientation provide complementary checks. {{scenario_background}} {{mode_background}}",
        _p_lotka,
        (
            _step("rhs", "Evaluate the two-component Lotka-Volterra vector field for prey and predator populations without modifying the state.", "def {{fn}}(state: np.ndarray, alpha: float, beta: float, delta: float, gamma: float) -> np.ndarray:", (
                "state = np.array([10.0, 2.0]); out = {{fn}}(state, {{alpha}}, {{beta}}, {{delta}}, {{gamma}})\nassert out.shape == (2,)",
                "state = np.zeros(2); out = {{fn}}(state, {{alpha}}, {{beta}}, {{delta}}, {{gamma}})\nassert np.allclose(out, 0.0)",
                "state = np.array([1.5, 0.7]); out = {{fn}}(state, {{alpha}}, {{beta}}, {{delta}}, {{gamma}})\nassert np.allclose(out, target)",
            ), "return derivative", "The prey growth term is alpha*x, predation is beta*x*y, and predator loss is gamma*y."),
            _step("integrate", "Integrate the population state over a time grid with the selected solver and return the time array and trajectory. Reuse {{fn_rhs}}.", "def {{fn}}(initial: np.ndarray, times: np.ndarray, alpha: float, beta: float, delta: float, gamma: float) -> tuple[np.ndarray, np.ndarray]:", (
                "times = np.linspace(0.0, 4.0, 12); out = {{fn}}(np.array([10.0, 2.0]), times, {{alpha}}, {{beta}}, {{delta}}, {{gamma}})\nassert out[0].shape == times.shape and out[1].shape == (times.size, 2)",
                "times = np.array([0.0, 0.1]); out = {{fn}}(np.array([0.0, 0.0]), times, {{alpha}}, {{beta}}, {{delta}}, {{gamma}})\nassert np.allclose(out[1], 0.0)",
                "times = np.linspace(0.0, 2.0, 8); out = {{fn}}(np.array([2.0, 1.0]), times, {{alpha}}, {{beta}}, {{delta}}, {{gamma}})\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return times, trajectory", "Adaptive solvers still need a deterministic output grid; interpolation must not reorder requested times."),
            _step("diagnostics", "Return the trajectory, positive equilibrium, cycle area estimate, and {{diagnostic_label}}.", "def {{fn}}(initial: np.ndarray, times: np.ndarray, alpha: float, beta: float, delta: float, gamma: float) -> tuple[np.ndarray, np.ndarray, float, float]:", (
                "times = np.linspace(0.0, 3.0, 16); out = {{fn}}(np.array([10.0, 2.0]), times, {{alpha}}, {{beta}}, {{delta}}, {{gamma}})\nassert out[0].shape == times.shape and np.all(out[1] >= 0.0)",
                "times = np.linspace(0.0, 1.0, 5); out = {{fn}}(np.array([1.0, 1.0]), times, {{alpha}}, {{beta}}, {{delta}}, {{gamma}})\nassert np.allclose(np.asarray(out[1:]), np.asarray(target[1:]))",
                "times = np.linspace(0.0, 2.0, 8); out = {{fn}}(np.array([2.0, 0.5]), times, {{alpha}}, {{beta}}, {{delta}}, {{gamma}})\nassert np.all(np.isfinite(np.asarray(out)))",
            ), "return times, trajectory, equilibrium, diagnostic", "The positive fixed point is (gamma/delta, alpha/beta). Cycle area depends on the chosen phase-space orientation and sampling grid."),
        ),
        (_mode("rk45", "adaptive RK45", "Use an adaptive explicit Runge-Kutta method and interpolate onto the requested output grid.", "Adaptive error control is useful when population time scales differ."), _mode("rk4", "fixed-step RK4", "Use a fixed-step classical RK4 integrator and map results to the requested grid.", "A fixed-step method makes reproducibility transparent but requires a stable step.")),
        ("positive", "bounded", "stiff", "ensemble"), ("invariant", "stability", "sensitivity"),
        "SciPy integrate reference API", "https://github.com/scipy/scipy", ("biology", "ode", "population"),
    ),
    _recipe(
        "sir_inference", "biology", "epidemiology",
        "SIR outbreak simulation and reproduction-number fit",
        "Simulate a susceptible-infected-recovered model, fit a transmission rate to observations, and report the inferred basic reproduction number. {{scenario_description}} {{mode_description}}",
        "Use population={{population}}, beta={{beta}}, recovery rate gamma={{gamma}}, and a {{days}}-day observation window. Compartments are counts or fractions, but the choice must remain consistent.",
        "import numpy as np\nfrom scipy.optimize import least_squares",
        "Compartment models conserve total population when births and deaths are absent. A fit must distinguish the transmission rate from the reporting scale. {{scenario_background}} {{mode_background}}",
        _p_sir,
        (
            _step("rhs", "Evaluate the SIR derivative for susceptible, infected, and recovered compartments under the declared population convention.", "def {{fn}}(state: np.ndarray, beta: float, gamma: float, population: float) -> np.ndarray:", (
                "state = np.array([{{population}} - 10.0, 10.0, 0.0]); out = {{fn}}(state, {{beta}}, {{gamma}}, {{population}})\nassert out.shape == (3,)",
                "state = np.array([0.0, 0.0, {{population}}]); out = {{fn}}(state, {{beta}}, {{gamma}}, {{population}})\nassert np.allclose(out, 0.0)",
                "state = np.array([800.0, 150.0, 50.0]); out = {{fn}}(state, {{beta}}, {{gamma}}, 1000.0)\nassert np.allclose(out, target)",
            ), "return derivative", "The infection flux is beta*S*I/N. The sum of the three derivatives must vanish."),
            _step("simulate", "Integrate the SIR system on an integer-day grid and return compartments and incidence. Reuse {{fn_rhs}}.", "def {{fn}}(initial: np.ndarray, days: int, beta: float, gamma: float, population: float) -> tuple[np.ndarray, np.ndarray]:", (
                "out = {{fn}}(np.array([{{population}} - 1.0, 1.0, 0.0]), {{days}}, {{beta}}, {{gamma}}, {{population}})\nassert out[0].shape == ({{days}} + 1,) and out[1].shape == ({{days}} + 1, 3)",
                "out = {{fn}}(np.array([100.0, 0.0, 0.0]), 4, {{beta}}, {{gamma}}, 100.0)\nassert np.allclose(out[1][:, 1], 0.0)",
                "out = {{fn}}(np.array([800.0, 150.0, 50.0]), 6, {{beta}}, {{gamma}}, 1000.0)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return times, compartments", "Incidence is the discrete change in the recovered compartment plus the reporting convention; do not silently use a shifted time index."),
            _step("fit", "Fit beta and gamma to an infected-count series with weighted residuals, then return parameters, R0, and {{diagnostic_label}}.", "def {{fn}}(observed_infected: np.ndarray, population: float, initial: np.ndarray, dt: float) -> tuple[np.ndarray, float, float]:", (
                "observed = np.array([1.0, 2.0, 4.0, 7.0]); out = {{fn}}(observed, 1000.0, np.array([999.0, 1.0, 0.0]), 1.0)\nassert out[0].shape == (2,) and out[1] >= 0.0",
                "observed = np.zeros(5); out = {{fn}}(observed, 100.0, np.array([100.0, 0.0, 0.0]), 1.0)\nassert np.all(np.isfinite(out))",
                "observed = np.array([10.0, 15.0, 23.0, 35.0, 50.0]); out = {{fn}}(observed, {{population}}, np.array([{{population}} - 10.0, 10.0, 0.0]), 1.0)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return fitted_rates, reproduction_number, diagnostic", "For a frequency-dependent SIR model, R0 is beta/gamma at the fitted parameters."),
        ),
        (_mode("deterministic", "deterministic ODE fit", "Fit the deterministic compartment trajectory directly.", "The deterministic model exposes conservation and reporting-scale assumptions."), _mode("latent", "latent-observation fit", "Fit a latent trajectory with a separate observation scale.", "A latent observation model can prevent reporting undercount from biasing beta.")),
        ("positive", "noisy", "bounded", "stiff"), ("invariant", "likelihood", "uncertainty"),
        "SciPy optimize and integrate reference APIs", "https://github.com/scipy/scipy", ("biology", "compartment-model", "inference"),
    ),
    _recipe(
        "hill_response", "biology", "pharmacology",
        "Hill dose-response curve and potency uncertainty",
        "Evaluate a Hill dose-response curve, fit potency and cooperativity to measurements, and compute a confidence-width diagnostic. {{scenario_description}} {{mode_description}}",
        "Use {{points}} positive doses, EC50={{ec50}}, Hill coefficient={{hill}}, upper response={{top}}, and a known response scale. Keep dose and response units explicit.",
        "import numpy as np\nfrom scipy.optimize import least_squares",
        "The Hill equation maps dose to a bounded response and is nonlinear in log dose. Weighting and parameter constraints determine whether potency is identifiable. {{scenario_background}} {{mode_background}}",
        _p_hill,
        (
            _step("curve", "Evaluate the four-parameter Hill response for positive doses, including the lower asymptote supplied by the function arguments.", "def {{fn}}(dose: np.ndarray, bottom: float, top: float, ec50: float, hill: float) -> np.ndarray:", (
                "dose = np.logspace(-2, 2, {{points}}); out = {{fn}}(dose, 0.0, {{top}}, {{ec50}}, {{hill}})\nassert out.shape == dose.shape and np.all(out >= 0.0)",
                "dose = np.array([{{ec50}} * 0.01, {{ec50}}, {{ec50}} * 100.0]); out = {{fn}}(dose, 0.0, {{top}}, {{ec50}}, {{hill}})\nassert np.isclose(out[1], {{top}} / 2.0, rtol=1e-5)",
                "dose = np.array([0.2, 0.8, 2.4]); out = {{fn}}(dose, 0.1, 1.7, {{ec50}}, {{hill}})\nassert np.allclose(out, target)",
            ), "return response", "At dose equal to EC50 the response is halfway between bottom and top, independent of the Hill coefficient."),
            _step("fit", "Fit bottom, top, EC50, and Hill coefficient under positivity and ordering constraints. Reuse {{fn_curve}} and return the fitted parameters.", "def {{fn}}(dose: np.ndarray, response: np.ndarray, sigma: np.ndarray) -> np.ndarray:", (
                "dose = np.logspace(-2, 2, {{points}}); y = {{fn_curve}}(dose, 0.0, {{top}}, {{ec50}}, {{hill}}); out = {{fn}}(dose, y, np.full(dose.size, 0.05))\nassert out.shape == (4,) and np.all(np.isfinite(out))",
                "dose = np.array([0.1, 1.0, 10.0]); response = np.array([0.1, 0.5, 0.9]); out = {{fn}}(dose, response, np.ones(3) * 0.1)\nassert out[2] > 0.0 and out[3] > 0.0",
                "dose = np.logspace(-1, 1, {{points}}); y = {{fn_curve}}(dose, 0.05, {{top}}, {{ec50}}, {{hill}}); out = {{fn}}(dose, y, np.full(dose.size, 0.03))\nassert np.allclose(out, target)",
            ), "return fitted_bottom, fitted_top, fitted_ec50, fitted_hill", "Fitting in log EC50 and log Hill coordinates enforces positivity while preserving the response scale."),
            _step("diagnostics", "Return fitted parameters, predicted responses, weighted RMS error, and {{diagnostic_label}}.", "def {{fn}}(dose: np.ndarray, response: np.ndarray, sigma: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:", (
                "dose = np.logspace(-2, 2, {{points}}); y = {{fn_curve}}(dose, 0.0, {{top}}, {{ec50}}, {{hill}}); out = {{fn}}(dose, y, np.full(dose.size, 0.05))\nassert out[1].shape == dose.shape and out[2] >= 0.0",
                "dose = np.array([0.1, 1.0, 10.0]); response = np.array([0.1, 0.5, 0.9]); out = {{fn}}(dose, response, np.ones(3) * 0.1)\nassert np.allclose(np.asarray(out), np.asarray(target))",
                "dose = np.logspace(-1, 1, 8); response = {{fn_curve}}(dose, 0.0, {{top}}, {{ec50}}, {{hill}}); out = {{fn}}(dose, response, np.full(dose.size, 0.1))\nassert np.all(np.isfinite(np.asarray(out)))",
            ), "return fitted_parameters, prediction, weighted_rms, diagnostic", "A confidence-width or sensitivity scalar reveals whether a visually good sigmoid is actually well constrained."),
        ),
        (_mode("log_dose", "log-dose fit", "Fit on logarithmic dose coordinates with a bounded least-squares objective.", "Log-dose coordinates distribute information more evenly across decades."), _mode("direct", "direct-dose fit", "Fit the dose-domain equation directly with explicit bounds.", "Direct coordinates retain physical units but can be poorly scaled.")),
        ("positive", "noisy", "irregular", "calibrated"), ("uncertainty", "sensitivity", "calibration"),
        "SciPy optimize reference API", "https://github.com/scipy/scipy", ("biology", "dose-response", "nonlinear-fit"),
    ),
    _recipe(
        "wright_fisher", "biology", "population-genetics",
        "Wright-Fisher allele-frequency drift",
        "Simulate neutral or selected Wright-Fisher allele-frequency trajectories, summarize fixation and heterozygosity, and estimate replicate uncertainty. {{scenario_description}} {{mode_description}}",
        "Use population size={{population}}, generations={{generations}}, initial allele frequency={{initial_frequency}}, and {{replicates}} independent replicates. A generation samples 2N gene copies.",
        "import numpy as np",
        "Genetic drift is a binomial sampling process. Selection changes the sampling probability before the draw; fixation and heterozygosity are population-level diagnostics. {{scenario_background}} {{mode_background}}",
        _p_wright,
        (
            _step("transition", "Compute the next allele frequency from a current frequency, diploid population size, and selection coefficient using a supplied random generator.", "def {{fn}}(frequency: float, population: int, selection: float, rng: np.random.Generator) -> float:", (
                "rng = np.random.default_rng(43); out = {{fn}}(0.5, {{population}}, 0.0, rng)\nassert 0.0 <= out <= 1.0",
                "rng = np.random.default_rng(43); out = {{fn}}(0.0, {{population}}, 0.2, rng)\nassert out == 0.0",
                "rng = np.random.default_rng(43); out = {{fn}}({{initial_frequency}}, {{population}}, 0.05, rng)\nassert np.allclose(out, target)",
            ), "return frequency_next", "The binomial draw has 2N trials and a success probability after the selection transform; it must use the caller's generator."),
            _step("trajectory", "Generate a frequency trajectory for the requested generations and seed. Reuse {{fn_transition}} and return the generation axis and frequencies.", "def {{fn}}(initial_frequency: float, population: int, generations: int, selection: float, seed: int) -> tuple[np.ndarray, np.ndarray]:", (
                "out = {{fn}}({{initial_frequency}}, {{population}}, 5, 0.0, 7)\nassert out[0].shape == (6,) and out[1].shape == (6,)",
                "out = {{fn}}(0.0, {{population}}, {{generations}}, 0.1, 7)\nassert np.allclose(out[1], 0.0)",
                "out = {{fn}}({{initial_frequency}}, {{population}}, {{generations}}, 0.02, 7)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return generations, frequencies", "A seed must initialize one isolated stream; reusing a global stream makes replicate summaries order-dependent."),
            _step("summary", "Aggregate replicate trajectories and return fixation probability, final heterozygosity, and {{diagnostic_label}}.", "def {{fn}}(initial_frequency: float, population: int, generations: int, replicates: int, selection: float, seed: int) -> tuple[float, float, float]:", (
                "out = {{fn}}({{initial_frequency}}, {{population}}, 4, 8, 0.0, 11)\nassert np.all(np.isfinite(out)) and 0.0 <= out[0] <= 1.0",
                "out = {{fn}}(0.0, {{population}}, 4, 4, 0.1, 11)\nassert np.allclose(np.asarray(out), np.asarray(target))",
                "out = {{fn}}({{initial_frequency}}, {{population}}, {{generations}}, {{replicates}}, 0.01, 11)\nassert out[1] >= 0.0",
            ), "return fixation_probability, heterozygosity, diagnostic", "Heterozygosity is 2p(1-p) at the final generation; replicate uncertainty must be reported separately from the mean."),
        ),
        (_mode("neutral", "neutral drift", "Set selection to zero and sample binomially around the current frequency.", "Neutral drift preserves the expected frequency but increases variance over generations."), _mode("selection", "genic selection", "Apply a multiplicative fitness advantage before binomial sampling.", "Selection changes the transition probability and can compete with drift.")),
        ("stochastic", "positive", "ensemble", "bounded"), ("uncertainty", "invariant", "sensitivity"),
        "msprime population-genetics reference project", "https://github.com/tskit-dev/msprime", ("biology", "stochastic", "population-genetics"),
    ),
    _recipe(
        "kepler_orbit", "astronomy", "celestial-mechanics",
        "Elliptic orbit elements and invariant propagation",
        "Solve Kepler's equation, convert orbital elements to a Cartesian state, and propagate the orbit while checking energy and angular momentum. {{scenario_description}} {{mode_description}}",
        "Use semimajor axis a={{a}}, eccentricity={{eccentricity}}, gravitational parameter mu={{mu}}, and propagation interval dt={{dt}}. Angles are radians and vectors use an inertial right-handed frame.",
        "import numpy as np\nfrom scipy.optimize import newton",
        "Two-body motion links anomaly variables, rotations, and conserved quantities. A correct implementation must state angle normalization and rotation order rather than relying on a library default. {{scenario_background}} {{mode_background}}",
        _p_kepler,
        (
            _step("anomaly", "Solve M=E-e*sin(E) for eccentric anomaly E with a bounded Newton or bracketed iteration and normalize the input mean anomaly.", "def {{fn}}(mean_anomaly: float, eccentricity: float, tolerance: float = 1e-12, max_iterations: int = 80) -> float:", (
                "out = {{fn}}(0.0, {{eccentricity}})\nassert np.allclose(out, 0.0)",
                "out = {{fn}}(2.0 * np.pi + 0.37, {{eccentricity}})\nassert np.isfinite(out)",
                "out = {{fn}}(-1.2, min({{eccentricity}} * 0.7, 0.7))\nassert np.allclose(out, target)",
            ), "return eccentric_anomaly", "The derivative 1-e*cos(E) stays positive for an elliptic orbit but can become poorly conditioned near e=1."),
            _step("state", "Convert classical elliptic elements to position and velocity in an inertial frame using the selected rotation convention. Reuse {{fn_anomaly}}.", "def {{fn}}(semi_major_axis: float, eccentricity: float, inclination: float, raan: float, argument_of_periapsis: float, mean_anomaly: float, mu: float) -> tuple[np.ndarray, np.ndarray]:", (
                "out = {{fn}}({{a}}, {{eccentricity}}, 0.2, 0.4, 0.7, 1.1, {{mu}})\nassert out[0].shape == (3,) and out[1].shape == (3,)",
                "out = {{fn}}({{a}} * 1.3, {{eccentricity}} * 0.5, 0.0, 0.0, 0.0, 0.0, {{mu}})\nassert np.all(np.isfinite(np.asarray(out)))",
                "out = {{fn}}({{a}} * 0.8, min({{eccentricity}} * 1.1, 0.9), 1.0, -0.5, 2.2, -0.8, {{mu}})\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return position, velocity", "The perifocal state follows from p=a(1-e squared), then a fixed R3-R1-R3 rotation maps it to inertial axes."),
            _step("propagate", "Propagate a Cartesian state by dt with the selected anomaly or universal-variable method and return energy and angular momentum diagnostics.", "def {{fn}}(position: np.ndarray, velocity: np.ndarray, dt: float, mu: float) -> tuple[np.ndarray, np.ndarray, float, float]:", (
                "r = np.array([{{a}}, 0.0, 0.0]); v = np.array([0.0, np.sqrt({{mu}} / {{a}}), 0.0]); out = {{fn}}(r, v, {{dt}}, {{mu}})\nassert out[0].shape == (3,) and np.all(np.isfinite(np.asarray(out[2:])))",
                "r = np.array([{{a}} * 1.2, 0.1, 0.0]); v = np.array([0.0, 0.6, 0.2]); out = {{fn}}(r, v, {{dt}} * 2.0, {{mu}})\nassert np.all(np.isfinite(np.asarray(out)))",
                "r = np.array([{{a}}, 0.0, 0.0]); v = np.array([0.0, np.sqrt({{mu}} / {{a}}), 0.0]); out = {{fn}}(r, v, {{dt}}, {{mu}})\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return position_new, velocity_new, specific_energy, angular_momentum", "Specific energy is v squared over two minus mu over r; angular momentum is r cross v. Both are invariant under exact propagation."),
        ),
        (_mode("newton", "Newton anomaly solve", "Use Newton iterations initialized at the mean anomaly.", "Newton convergence requires a bounded iteration count and a residual check."), _mode("universal", "universal-variable propagation", "Use universal variables for the propagation step.", "Universal variables avoid switching conic formulas but require a Stumpff-function convention.")),
        ("bounded", "periodic", "calibrated", "irregular"), ("invariant", "stability", "sensitivity"),
        "Astropy coordinates and orbital-dynamics reference project", "https://github.com/astropy/astropy", ("astronomy", "orbit", "invariants"),
    ),
    _recipe(
        "photometry_period", "astronomy", "time-series-astronomy",
        "Irregular photometric period search and phase folding",
        "Estimate a dominant period from irregular brightness measurements, fold the light curve, and report a false-alarm or coherence diagnostic. {{scenario_description}} {{mode_description}}",
        "Use {{points}} observations over a baseline of {{baseline}}, injected period={{period}}, and noise scale={{noise}}. Times are strictly increasing but need not be equally spaced.",
        "import numpy as np\nfrom scipy.signal import lombscargle",
        "Astronomical cadence is irregular and often heteroscedastic. A periodogram must specify angular frequency, centering, and normalization before a peak can be interpreted. {{scenario_background}} {{mode_background}}",
        _p_photometry,
        (
            _step("periodogram", "Compute a normalized Lomb-Scargle power spectrum for irregular times and magnitudes over a supplied frequency grid.", "def {{fn}}(times: np.ndarray, magnitudes: np.ndarray, frequencies: np.ndarray) -> np.ndarray:", (
                "times = np.linspace(0.0, {{baseline}}, {{points}}); y = np.sin(2.0 * np.pi * times / {{period}}); f = np.linspace(0.01, 2.0, 40); out = {{fn}}(times, y, f)\nassert out.shape == f.shape and np.all(out >= 0.0)",
                "times = np.array([0.0, 0.7, 2.1, 4.0]); y = np.array([1.0, 0.2, -0.4, 0.8]); f = np.array([0.1, 0.3, 0.8]); out = {{fn}}(times, y, f)\nassert np.all(np.isfinite(out))",
                "times = np.linspace(0.0, 10.0, 12) ** 1.03; y = np.cos(times); f = np.linspace(0.05, 1.0, 16); out = {{fn}}(times, y, f)\nassert np.allclose(out, target)",
            ), "return power", "Centering the observations and using angular frequencies avoids a scale-dependent offset in the periodogram."),
            _step("fold", "Select the strongest candidate period, fold the observations into phase in [0,1), and return sorted phase and brightness arrays.", "def {{fn}}(times: np.ndarray, magnitudes: np.ndarray, candidate_period: float) -> tuple[np.ndarray, np.ndarray]:", (
                "times = np.linspace(0.0, {{baseline}}, {{points}}); y = np.sin(2.0 * np.pi * times / {{period}}); out = {{fn}}(times, y, {{period}})\nassert out[0].shape == times.shape and 0.0 <= out[0].min() < out[0].max() < 1.0",
                "times = np.array([3.2, 0.1, 1.7]); y = np.array([2.0, 1.0, 1.5]); out = {{fn}}(times, y, 1.0)\nassert np.all(np.diff(out[0]) >= 0.0)",
                "times = np.array([0.0, 0.5, 1.0]); y = np.array([1.0, 2.0, 3.0]); out = {{fn}}(times, y, 0.5)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return phase_sorted, magnitude_sorted", "Phase is fractional time modulo the candidate period. Sorting is required before a binned folded curve is formed."),
            _step("diagnostics", "Return the frequency grid, selected period, folded scatter, and {{diagnostic_label}} for the light curve.", "def {{fn}}(times: np.ndarray, magnitudes: np.ndarray, frequency_grid: np.ndarray) -> tuple[np.ndarray, float, float, float]:", (
                "times = np.linspace(0.0, {{baseline}}, {{points}}); y = np.sin(2.0 * np.pi * times / {{period}}); grid = np.linspace(0.01, 2.0, 40); out = {{fn}}(times, y, grid)\nassert out[0].shape == grid.shape and np.isfinite(out[1:]).all()",
                "times = np.array([0.0, 1.0, 2.0, 3.0]); y = np.array([1.0, 0.0, 1.0, 0.0]); grid = np.array([0.2, 0.5, 1.0]); out = {{fn}}(times, y, grid)\nassert np.allclose(np.asarray(out[1:]), np.asarray(target[1:]))",
                "times = np.linspace(0.0, 20.0, 24) ** 1.02; y = np.cos(times) + {{noise}}; grid = np.linspace(0.02, 1.0, 18); out = {{fn}}(times, y, grid)\nassert np.all(np.isfinite(np.asarray(out)))",
            ), "return power, best_period, folded_scatter, diagnostic", "A period peak is not sufficient evidence; folded scatter and a false-alarm or coherence statistic expose aliasing."),
        ),
        (_mode("classic", "classic Lomb-Scargle", "Use the centered classic Lomb-Scargle normalization.", "Classic normalization depends on the variance of the centered observations."), _mode("floating_mean", "floating-mean periodogram", "Fit a floating mean at each trial frequency.", "A floating mean handles uneven phase coverage but changes the null distribution.")),
        ("irregular", "noisy", "bounded", "calibrated"), ("spectrum", "uncertainty", "robustness"),
        "Astropy Lomb-Scargle reference implementation", "https://github.com/astropy/astropy", ("astronomy", "period-search", "irregular-time"),
    ),
    _recipe(
        "seismic_ray", "geoscience", "seismology",
        "Layered-medium seismic ray travel time",
        "Compute a refracted ray through horizontal velocity layers, evaluate travel-time and offset derivatives, and report a residual diagnostic. {{scenario_description}} {{mode_description}}",
        "Use {{layers}} layers with representative depth={{depth}}, velocity={{velocity}}, and source-receiver offset={{offset}}. Velocities are positive and layer thicknesses sum to the model depth.",
        "import numpy as np\nfrom scipy.optimize import brentq",
        "Snell's law couples ray angle, horizontal slowness, and layer thickness. Critical-angle handling and monotonic branch selection are essential in a layered earth model. {{scenario_background}} {{mode_background}}",
        _p_seismic,
        (
            _step("slowness", "Compute horizontal offset and travel time for a trial horizontal slowness through layers, rejecting supercritical rays.", "def {{fn}}(horizontal_slowness: float, thickness: np.ndarray, velocity: np.ndarray) -> tuple[float, float]:", (
                "thickness = np.full({{layers}}, {{depth}} / {{layers}}); velocity = np.full({{layers}}, {{velocity}}); out = {{fn}}(0.0, thickness, velocity)\nassert np.allclose(out[0], 0.0) and out[1] > 0.0",
                "thickness = np.array([1.0, 2.0]); velocity = np.array([2.0, 3.0]); out = {{fn}}(0.1, thickness, velocity)\nassert np.all(np.isfinite(out))",
                "thickness = np.array([2.0, 4.0, 3.0]); velocity = np.array([2.5, 3.0, 4.0]); out = {{fn}}(0.2, thickness, velocity)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return offset, travel_time", "For each layer sin(theta)=p*v. The square-root branch must remain real and positive."),
            _step("invert", "Find the horizontal slowness that matches the requested offset by a bracketed root solve and return the ray path angles.", "def {{fn}}(target_offset: float, thickness: np.ndarray, velocity: np.ndarray) -> tuple[float, np.ndarray]:", (
                "thickness = np.full({{layers}}, {{depth}} / {{layers}}); velocity = np.full({{layers}}, {{velocity}}); out = {{fn}}({{offset}}, thickness, velocity)\nassert out[0] >= 0.0 and out[1].shape == thickness.shape",
                "thickness = np.array([1.0, 1.0]); velocity = np.array([2.0, 4.0]); out = {{fn}}(0.0, thickness, velocity)\nassert np.allclose(out[1], 0.0)",
                "thickness = np.array([2.0, 3.0, 4.0]); velocity = np.array([2.5, 3.0, 4.0]); out = {{fn}}(5.0, thickness, velocity)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return horizontal_slowness, ray_angles", "The physically admissible root lies below the smallest critical slowness; a bracket should make that restriction explicit."),
            _step("diagnostics", "Return ray slowness, travel time, offset residual, and {{diagnostic_label}} for the layered model.", "def {{fn}}(target_offset: float, thickness: np.ndarray, velocity: np.ndarray) -> tuple[float, float, float, float]:", (
                "thickness = np.full({{layers}}, {{depth}} / {{layers}}); velocity = np.full({{layers}}, {{velocity}}); out = {{fn}}({{offset}}, thickness, velocity)\nassert np.all(np.isfinite(out))",
                "thickness = np.array([1.0, 2.0, 1.5]); velocity = np.array([2.0, 2.5, 3.0]); out = {{fn}}(3.0, thickness, velocity)\nassert np.allclose(np.asarray(out[1:]), np.asarray(target[1:]))",
                "thickness = np.array([2.0, 3.0]); velocity = np.array([3.0, 4.0]); out = {{fn}}(1.0, thickness, velocity)\nassert np.isfinite(out[3])",
            ), "return horizontal_slowness, travel_time, offset_residual, diagnostic", "Travel-time residual and a sensitivity or conditioning estimate distinguish a valid ray from a branch-selected numerical root."),
        ),
        (_mode("ray_parameter", "ray-parameter inversion", "Solve in horizontal slowness with Snell's law.", "Ray-parameter coordinates make layer interfaces continuous."), _mode("angle", "angle shooting", "Shoot an initial angle and adjust it to match the receiver offset.", "Angle shooting must reject rays that exceed a layer's critical angle.")),
        ("bounded", "anisotropic", "irregular", "calibrated"), ("stability", "sensitivity", "calibration"),
        "ObsPy seismology reference project", "https://github.com/obspy/obspy", ("geoscience", "seismic", "ray-tracing"),
    ),
    _recipe(
        "spherical_harmonics", "geoscience", "planetary-geodesy",
        "Real spherical-harmonic field synthesis",
        "Synthesize a scalar field on a spherical grid from real harmonic coefficients, recover its power by degree, and quantify truncation error. {{scenario_description}} {{mode_description}}",
        "Use maximum degree {{degree}}, {{samples}} latitude-longitude samples, radius={{radius}}, and optional coefficient noise={{noise}}. Angles are colatitude and longitude in radians.",
        "import numpy as np\nfrom scipy.special import sph_harm_y",
        "Spherical harmonics separate angular structure into orthogonal degrees and orders. Normalization, longitude convention, and the real-versus-complex basis must be fixed in the interface. {{scenario_background}} {{mode_background}}",
        _p_harmonics,
        (
            _step("basis", "Evaluate the requested real spherical-harmonic basis functions on a theta-phi grid with a stated normalization.", "def {{fn}}(theta: np.ndarray, phi: np.ndarray, degree: int, order: int) -> np.ndarray:", (
                "theta = np.linspace(0.1, np.pi - 0.1, 8); phi = np.linspace(-np.pi, np.pi, 8); out = {{fn}}(theta, phi, 2, 1)\nassert out.shape == theta.shape and np.all(np.isfinite(out))",
                "theta = np.array([0.0, np.pi / 2.0, np.pi]); phi = np.zeros(3); out = {{fn}}(theta, phi, 1, 0)\nassert np.allclose(out, target)",
                "theta = np.array([0.2, 0.7]); phi = np.array([1.0, -1.0]); out = {{fn}}(theta, phi, {{degree}}, 2)\nassert np.allclose(out, target)",
            ), "return basis_values", "The associated Legendre convention controls the phase and normalization; it must be consistent across synthesis and power recovery."),
            _step("synthesize", "Synthesize the field from real cosine and sine coefficients through degree {{degree}} and return the grid field. Reuse {{fn_basis}}.", "def {{fn}}(theta: np.ndarray, phi: np.ndarray, cosine_coefficients: np.ndarray, sine_coefficients: np.ndarray, radius: float) -> np.ndarray:", (
                "theta = np.linspace(0.1, np.pi - 0.1, 6); phi = np.linspace(-np.pi, np.pi, 6); c = np.zeros(({{degree}} + 1, {{degree}} + 1)); s = np.zeros_like(c); out = {{fn}}(theta, phi, c, s, {{radius}})\nassert out.shape == theta.shape and np.allclose(out, 0.0)",
                "theta = np.array([np.pi / 2.0]); phi = np.array([0.0]); c = np.zeros((3, 3)); c[1, 0] = 2.0; s = np.zeros_like(c); out = {{fn}}(theta, phi, c, s, 1.0)\nassert np.all(np.isfinite(out))",
                "theta = np.array([0.3, 0.8]); phi = np.array([0.2, 1.1]); c = np.ones(({{degree}} + 1, {{degree}} + 1)) * 0.01; s = np.zeros_like(c); out = {{fn}}(theta, phi, c, s, {{radius}})\nassert np.allclose(out, target)",
            ), "return field", "Only coefficients with order not exceeding degree are valid. Radius scaling depends on the chosen physical field convention."),
            _step("power", "Recover degree power from the coefficient arrays and return total power, truncation remainder proxy, and {{diagnostic_label}}.", "def {{fn}}(cosine_coefficients: np.ndarray, sine_coefficients: np.ndarray, degree: int) -> tuple[np.ndarray, float, float]:", (
                "c = np.zeros(({{degree}} + 1, {{degree}} + 1)); s = np.zeros_like(c); c[1, 0] = 1.0; out = {{fn}}(c, s, {{degree}})\nassert out[0].shape == ({{degree}} + 1,) and out[1] >= 0.0",
                "c = np.ones((3, 3)); s = np.zeros_like(c); out = {{fn}}(c, s, 2)\nassert np.allclose(np.asarray(out), np.asarray(target))",
                "c = np.zeros(({{degree}} + 1, {{degree}} + 1)); s = np.zeros_like(c); out = {{fn}}(c, s, {{degree}})\nassert np.all(np.isfinite(np.asarray(out)))",
            ), "return degree_power, total_power, diagnostic", "Orthogonality makes total power the sum of degree powers under the declared normalization; omitted degrees form the truncation remainder."),
        ),
        (_mode("fully_normalized", "fully normalized basis", "Use the fully normalized real basis.", "Fully normalized coefficients make degree power comparable across orders."), _mode("schmidt", "Schmidt semi-normalized basis", "Use Schmidt semi-normalization consistently for synthesis and power.", "Schmidt normalization is common in geomagnetism but changes coefficient magnitudes.")),
        ("bounded", "multichannel", "anisotropic", "calibrated"), ("spectrum", "robustness", "calibration"),
        "pyshtools spherical-harmonic reference project", "https://github.com/SHTOOLS/SHTOOLS", ("geoscience", "harmonics", "field-synthesis"),
    ),
    _recipe(
        "kalman_filter", "statistics", "state-estimation",
        "Linear state-space filtering with uncertainty propagation",
        "Implement a linear Kalman filter, smooth a noisy trajectory, and report an innovation likelihood and covariance diagnostic. {{scenario_description}} {{mode_description}}",
        "Use {{steps}} time steps, interval dt={{dt}}, process variance={{process_noise}}, and measurement variance={{measurement_noise}}. State and observation matrices are supplied by the caller.",
        "import numpy as np\nfrom scipy.linalg import solve_discrete_are",
        "The Kalman recursion separates prediction uncertainty from measurement uncertainty. Positive semidefinite covariance and a consistent innovation convention are required. {{scenario_background}} {{mode_background}}",
        _p_kalman,
        (
            _step("predict", "Perform one linear state prediction and propagate its covariance with the supplied transition matrix and process covariance.", "def {{fn}}(state: np.ndarray, covariance: np.ndarray, transition: np.ndarray, process_covariance: np.ndarray) -> tuple[np.ndarray, np.ndarray]:", (
                "x = np.array([1.0, 0.2]); p = np.eye(2); a = np.array([[1.0, {{dt}}], [0.0, 1.0]]); q = np.eye(2) * {{process_noise}}; out = {{fn}}(x, p, a, q)\nassert out[0].shape == x.shape and out[1].shape == p.shape",
                "x = np.zeros(1); p = np.zeros((1, 1)); out = {{fn}}(x, p, np.ones((1, 1)), np.zeros((1, 1)))\nassert np.allclose(out[0], 0.0)",
                "x = np.array([0.4, -0.3]); p = np.array([[0.8, 0.1], [0.1, 0.5]]); a = np.array([[1.0, 0.2], [-0.1, 0.9]]); q = np.eye(2) * 0.03; out = {{fn}}(x, p, a, q)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return predicted_state, predicted_covariance", "The covariance update is A P A transpose plus Q. Symmetrization should be deliberate rather than silently hiding an indexing error."),
            _step("update", "Apply a measurement update, compute the innovation and its covariance, and return the posterior state and covariance. Reuse {{fn_predict}}.", "def {{fn}}(predicted_state: np.ndarray, predicted_covariance: np.ndarray, observation: np.ndarray, observation_matrix: np.ndarray, observation_covariance: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:", (
                "x = np.array([1.0, 0.0]); p = np.eye(2); h = np.array([[1.0, 0.0]]); r = np.array([[{{measurement_noise}}]]); out = {{fn}}(x, p, np.array([1.2]), h, r)\nassert out[0].shape == x.shape and out[2].shape == (1,)",
                "x = np.zeros(1); p = np.zeros((1, 1)); out = {{fn}}(x, p, np.array([0.0]), np.ones((1, 1)), np.eye(1))\nassert np.allclose(out[0], 0.0)",
                "x = np.array([0.4, -0.3]); p = np.eye(2); h = np.array([[0.7, -0.2]]); r = np.array([[0.1]]); out = {{fn}}(x, p, np.array([0.8]), h, r)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return filtered_state, filtered_covariance, innovation, innovation_covariance", "The Kalman gain uses the inverse innovation covariance. Joseph-form covariance is useful when numerical symmetry matters."),
            _step("smooth", "Filter a complete observation sequence and return smoothed states, log likelihood, and {{diagnostic_label}}.", "def {{fn}}(observations: np.ndarray, initial_state: np.ndarray, transition: np.ndarray, observation_matrix: np.ndarray, process_covariance: np.ndarray, observation_covariance: np.ndarray) -> tuple[np.ndarray, float, float]:", (
                "y = np.zeros(({{steps}}, 1)); out = {{fn}}(y, np.zeros(2), np.array([[1.0, {{dt}}], [0.0, 1.0]]), np.array([[1.0, 0.0]]), np.eye(2) * {{process_noise}}, np.eye(1) * {{measurement_noise}})\nassert out[0].shape == ({{steps}}, 2)",
                "y = np.array([[0.0], [1.0], [0.5]]); out = {{fn}}(y, np.zeros(1), np.ones((1, 1)), np.ones((1, 1)), np.zeros((1, 1)), np.eye(1))\nassert np.isfinite(out[1])",
                "y = np.sin(np.arange({{steps}})[:, None] * 0.2); out = {{fn}}(y, np.zeros(2), np.array([[1.0, {{dt}}], [0.0, 1.0]]), np.array([[1.0, 0.0]]), np.eye(2) * {{process_noise}}, np.eye(1) * {{measurement_noise}})\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return smoothed_states, log_likelihood, diagnostic", "The likelihood is the sum of Gaussian innovation log densities. A smoother must use stored predicted and filtered covariances in the correct time direction."),
        ),
        (_mode("standard", "standard covariance recursion", "Use the conventional predict-update covariance recursion.", "The standard form is concise but may need explicit symmetrization."), _mode("joseph", "Joseph stabilized update", "Use the Joseph covariance form for the measurement update.", "The Joseph form better preserves positive semidefiniteness under roundoff.")),
        ("multichannel", "noisy", "stiff", "irregular"), ("uncertainty", "likelihood", "stability"),
        "FilterPy state-estimation reference project", "https://github.com/rlabbe/filterpy", ("statistics", "kalman", "state-space"),
    ),
    _recipe(
        "gaussian_process", "statistics", "spatial-statistics",
        "Gaussian-process regression with predictive uncertainty",
        "Construct a positive-definite kernel matrix, evaluate a Gaussian-process marginal likelihood, and make predictive mean and variance estimates. {{scenario_description}} {{mode_description}}",
        "Use {{points}} one-dimensional observations, length scale={{length_scale}}, noise standard deviation={{noise}}, and jitter={{jitter}}. Inputs can be irregularly spaced.",
        "import numpy as np\nfrom scipy.linalg import cho_factor, cho_solve",
        "Gaussian-process regression is a numerical linear-algebra problem disguised as a statistical model. Kernel symmetry, jitter, and the distinction between latent and observation variance must be explicit. {{scenario_background}} {{mode_background}}",
        _p_gp,
        (
            _step("kernel", "Evaluate a squared-exponential covariance matrix between two one-dimensional coordinate arrays and add observation noise only when requested.", "def {{fn}}(x_left: np.ndarray, x_right: np.ndarray, length_scale: float, variance: float = 1.0) -> np.ndarray:", (
                "x = np.linspace(0.0, 1.0, {{points}}); out = {{fn}}(x, x, {{length_scale}})\nassert out.shape == ({{points}}, {{points}}) and np.allclose(out, out.T)",
                "out = {{fn}}(np.array([0.0]), np.array([1.0]), {{length_scale}})\nassert out.shape == (1, 1) and out[0, 0] > 0.0",
                "out = {{fn}}(np.array([0.0, 0.4]), np.array([0.2, 0.9]), {{length_scale}}, 1.7)\nassert np.allclose(out, target)",
            ), "return covariance", "The squared-exponential kernel depends on squared distance divided by twice the length-scale squared; variance scales the whole matrix."),
            _step("likelihood", "Compute the GP log marginal likelihood and solve for the whitened weights using a Cholesky factorization. Reuse {{fn_kernel}}.", "def {{fn}}(x: np.ndarray, y: np.ndarray, length_scale: float, noise: float, jitter: float) -> tuple[float, np.ndarray]:", (
                "x = np.linspace(0.0, 1.0, {{points}}); y = np.sin(x); out = {{fn}}(x, y, {{length_scale}}, {{noise}}, {{jitter}})\nassert np.isfinite(out[0]) and out[1].shape == x.shape",
                "x = np.array([0.0, 1.0]); y = np.array([1.0, 1.0]); out = {{fn}}(x, y, {{length_scale}}, 0.1, {{jitter}})\nassert np.all(np.isfinite(out[1]))",
                "x = np.array([0.0, 0.3, 1.2]); y = np.array([0.2, -0.1, 0.8]); out = {{fn}}(x, y, {{length_scale}}, {{noise}}, {{jitter}})\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return log_marginal_likelihood, alpha", "The log likelihood is -y transpose alpha over two minus the log determinant over two minus n log(2 pi) over two. Cholesky avoids an explicit inverse."),
            _step("predict", "Return predictive mean, latent variance, observation variance, and {{diagnostic_label}} at test coordinates.", "def {{fn}}(x_train: np.ndarray, y_train: np.ndarray, x_test: np.ndarray, length_scale: float, noise: float, jitter: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:", (
                "x = np.linspace(0.0, 1.0, {{points}}); y = np.sin(x); xt = np.linspace(-0.1, 1.1, 5); out = {{fn}}(x, y, xt, {{length_scale}}, {{noise}}, {{jitter}})\nassert out[0].shape == xt.shape and np.all(out[1] >= -1e-10)",
                "out = {{fn}}(np.array([0.0]), np.array([1.0]), np.array([0.0, 1.0]), {{length_scale}}, {{noise}}, {{jitter}})\nassert np.all(np.isfinite(np.asarray(out)))",
                "x = np.array([0.0, 0.4, 0.9]); y = np.array([0.2, -0.1, 0.8]); xt = np.array([0.1, 0.5]); out = {{fn}}(x, y, xt, {{length_scale}}, {{noise}}, {{jitter}})\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return predictive_mean, latent_variance, observation_variance, diagnostic", "Observation variance adds the noise variance to latent variance. Negative tiny values may be clipped only after the numerical convention is stated."),
        ),
        (_mode("cholesky", "Cholesky GP", "Use a Cholesky factor for solves and log determinant.", "Cholesky exposes positive-definiteness failures early."), _mode("eigendecomposition", "eigendecomposition GP", "Use a symmetric eigendecomposition with a jitter floor.", "Eigenvalue clipping can stabilize nearly singular designs but changes the effective kernel.")),
        ("irregular", "noisy", "positive", "bounded"), ("likelihood", "uncertainty", "stability"),
        "scikit-learn Gaussian-process reference project", "https://github.com/scikit-learn/scikit-learn", ("statistics", "gaussian-process", "kernel"),
    ),
    _recipe(
        "pca_whitening", "statistics", "multivariate-analysis",
        "Principal components, whitening, and reconstruction error",
        "Center a multivariate data matrix, compute principal components, whiten selected scores, and quantify reconstruction error. {{scenario_description}} {{mode_description}}",
        "Use {{samples}} samples and {{features}} features, retain {{components}} components, and optionally add noise scale={{noise}}. The sample axis is first.",
        "import numpy as np\nfrom scipy.linalg import eigh, svd",
        "PCA is defined by a covariance convention and an eigenvector sign ambiguity. Whitening and reconstruction must use the same centering statistics. {{scenario_background}} {{mode_background}}",
        _p_pca,
        (
            _step("center", "Center the data matrix by feature, compute the sample covariance, and return centered data and feature means.", "def {{fn}}(data: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:", (
                "rng = np.random.default_rng(47); data = rng.normal(size=({{samples}}, {{features}})); out = {{fn}}(data)\nassert out[0].shape == data.shape and out[1].shape == ({{features}},) and out[2].shape == ({{features}}, {{features}})",
                "data = np.ones((4, 2)); out = {{fn}}(data)\nassert np.allclose(out[0], 0.0) and np.allclose(out[2], 0.0)",
                "data = np.array([[1.0, 2.0], [3.0, 0.0], [2.0, 4.0]]); out = {{fn}}(data)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return centered_data, feature_mean, covariance", "The sample covariance divides by n-1. A population covariance changes eigenvalues and downstream whitening scales."),
            _step("components", "Compute the leading principal directions from the covariance or SVD, fix a deterministic sign convention, and return scores and explained variance.", "def {{fn}}(centered_data: np.ndarray, covariance: np.ndarray, component_count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:", (
                "rng = np.random.default_rng(53); data = rng.normal(size=({{samples}}, {{features}})); centered, _, cov = {{fn_center}}(data); out = {{fn}}(centered, cov, {{components}})\nassert out[0].shape == ({{samples}}, {{components}})",
                "centered = np.array([[1.0, 0.0], [-1.0, 0.0]]); cov = np.eye(2); out = {{fn}}(centered, cov, 1)\nassert out[0].shape == (2, 1)",
                "centered = np.array([[1.0, 2.0], [0.0, -1.0], [2.0, 0.5]]); cov = np.cov(centered, rowvar=False); out = {{fn}}(centered, cov, 1)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return scores, components, explained_variance", "Eigenvectors are ordered by descending eigenvalue. Fixing the largest-magnitude loading positive makes repeated runs comparable."),
            _step("whiten", "Whiten the retained scores, reconstruct the data from the retained components, and return RMS reconstruction error with {{diagnostic_label}}.", "def {{fn}}(scores: np.ndarray, components: np.ndarray, means: np.ndarray, original: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:", (
                "rng = np.random.default_rng(59); data = rng.normal(size=({{samples}}, {{features}})); centered, means, cov = {{fn_center}}(data); scores, components, _ = {{fn_components}}(centered, cov, {{components}}); out = {{fn}}(scores, components, means, data)\nassert out[0].shape == data.shape and out[1].shape == data.shape",
                "scores = np.array([[1.0], [-1.0]]); components = np.array([[1.0], [0.0]]); means = np.array([2.0, 3.0]); original = np.array([[3.0, 3.0], [1.0, 3.0]]); out = {{fn}}(scores, components, means, original)\nassert np.isfinite(out[2])",
                "scores = np.array([[0.2], [0.5], [-0.1]]); components = np.array([[1.0], [0.0]]); means = np.array([0.0, 1.0]); original = np.array([[0.2, 1.0], [0.5, 1.2], [-0.1, 0.8]]); out = {{fn}}(scores, components, means, original)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return whitened_scores, reconstruction, rms_error, diagnostic", "Whitening divides scores by square roots of retained eigenvalues; reconstruction must undo only the retained projection and then add means."),
        ),
        (_mode("eigh", "covariance eigendecomposition", "Diagonalize the symmetric covariance matrix.", "The covariance eigensystem is transparent and deterministic after sign fixing."), _mode("svd", "thin SVD", "Use a thin SVD of centered observations.", "SVD handles rank deficiency naturally but requires mapping singular values to covariance eigenvalues.")),
        ("multichannel", "noisy", "bounded", "ensemble"), ("spectrum", "stability", "robustness"),
        "scikit-learn decomposition reference project", "https://github.com/scikit-learn/scikit-learn", ("statistics", "pca", "whitening"),
    ),
    _recipe(
        "changepoint", "statistics", "time-series",
        "Piecewise-constant change-point segmentation",
        "Segment a one-dimensional scientific time series with a penalized dynamic program, reconstruct the piecewise signal, and report a change-point stability diagnostic. {{scenario_description}} {{mode_description}}",
        "Use {{points}} samples, penalty={{penalty}}, expected segments={{segments}}, and observation noise scale={{noise}}. Segment indices are zero-based and the final endpoint is exclusive.",
        "import numpy as np",
        "Change-point detection balances within-segment fit against model complexity. Prefix sums make segment costs exact and expose endpoint conventions that a greedy heuristic can hide. {{scenario_background}} {{mode_background}}",
        _p_changepoint,
        (
            _step("cost", "Compute the sum-of-squared-error cost for a half-open segment using prefix sums and a declared mean convention.", "def {{fn}}(prefix_sum: np.ndarray, prefix_square_sum: np.ndarray, start: int, stop: int) -> float:", (
                "x = np.array([1.0, 2.0, 3.0, 4.0]); out = {{fn}}(np.r_[0.0, np.cumsum(x)], np.r_[0.0, np.cumsum(x * x)], 0, 4)\nassert np.isclose(out, 5.0)",
                "x = np.ones({{points}}); out = {{fn}}(np.r_[0.0, np.cumsum(x)], np.r_[0.0, np.cumsum(x * x)], 2, 2)\nassert np.isclose(out, 0.0)",
                "x = np.array([0.2, 1.1, 0.4, 2.0]); out = {{fn}}(np.r_[0.0, np.cumsum(x)], np.r_[0.0, np.cumsum(x * x)], 1, 4)\nassert np.allclose(out, target)",
            ), "return segment_cost", "The cost is the residual sum of squares around the segment mean. Empty segments are invalid and must not be silently accepted."),
            _step("dynamic_program", "Find the penalized optimal segmentation and return sorted change-point indices and segment means. Reuse {{fn_cost}}.", "def {{fn}}(observations: np.ndarray, penalty: float, segment_limit: int) -> tuple[np.ndarray, np.ndarray]:", (
                "y = np.r_[np.zeros(8), np.ones(8) * 3.0]; out = {{fn}}(y, {{penalty}}, 4)\nassert np.all(np.diff(out[0]) > 0) and out[1].ndim == 1",
                "y = np.ones(5); out = {{fn}}(y, 0.0, 1)\nassert out[0].size == 0 or out[0].size == 1",
                "y = np.array([0.0, 0.1, 3.0, 3.1, 0.0]); out = {{fn}}(y, 0.5, 3)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return change_points, segment_means", "Dynamic programming must compare all admissible previous endpoints; a penalty of zero is a useful boundary case."),
            _step("diagnostics", "Reconstruct the fitted signal and return change points, residual variance, and {{diagnostic_label}}.", "def {{fn}}(observations: np.ndarray, penalty: float, segment_limit: int) -> tuple[np.ndarray, np.ndarray, float, float]:", (
                "y = np.r_[np.zeros(10), np.ones(10) * 2.0]; out = {{fn}}(y, {{penalty}}, {{segments}})\nassert out[0].ndim == 1 and out[1].shape == y.shape and out[2] >= 0.0",
                "y = np.ones(6); out = {{fn}}(y, 1.0, 2)\nassert np.allclose(np.asarray(out[1]), 1.0)",
                "y = np.sin(np.arange({{points}}) * 0.1) + {{noise}}; out = {{fn}}(y, {{penalty}}, {{segments}})\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return change_points, fitted_signal, residual_variance, diagnostic", "Residual variance and perturbation stability reveal whether a selected change point is supported by the data or only by the penalty."),
        ),
        (_mode("prefix_dp", "prefix-sum dynamic program", "Use prefix sums and an exact penalized dynamic program.", "Prefix sums give constant-time segment costs after one pass."), _mode("pruned_dp", "pruned dynamic program", "Use a pruned candidate set while preserving the same objective.", "Pruning is valid only when discarded candidates cannot become optimal later.")),
        ("bounded", "noisy", "irregular", "sparse"), ("robustness", "stability", "sensitivity"),
        "ruptures change-point reference project", "https://github.com/deepcharles/ruptures", ("statistics", "segmentation", "dynamic-programming"),
    ),
    _recipe(
        "mcmc_metropolis", "statistics", "bayesian-inference",
        "Metropolis sampling of a constrained posterior",
        "Evaluate a log posterior, run a reproducible random-walk Metropolis chain, and estimate posterior mean and effective sample size. {{scenario_description}} {{mode_description}}",
        "Use {{draws}} post-burn-in draws, proposal scale={{proposal}}, burn-in={{burn}}, and seed={{seed}}. The parameter is positive and the likelihood is evaluated in log space.",
        "import numpy as np\nfrom scipy.special import logsumexp",
        "MCMC correctness depends on the target density, proposal symmetry, acceptance bookkeeping, and autocorrelation. A chain with plausible marginals can still have a wrong burn-in or ESS. {{scenario_background}} {{mode_background}}",
        _p_mcmc,
        (
            _step("logposterior", "Evaluate the log posterior of a positive scalar parameter under a log-normal prior and Gaussian observation model.", "def {{fn}}(parameter: float, observations: np.ndarray, prior_scale: float) -> float:", (
                "out = {{fn}}(1.0, np.array([0.8, 1.1, 0.9]), 1.0)\nassert np.isfinite(out)",
                "out = {{fn}}(0.0, np.array([1.0]), 1.0)\nassert out == -np.inf",
                "out = {{fn}}(1.7, np.array([0.2, 0.4, 0.3]), 0.8)\nassert np.allclose(out, target)",
            ), "return log_posterior", "The log-normal prior includes the Jacobian term minus log(parameter). Reject nonpositive proposals before evaluating the likelihood."),
            _step("sample", "Run a random-walk Metropolis chain in log-parameter coordinates and return samples and the acceptance rate. Reuse {{fn_logposterior}}.", "def {{fn}}(observations: np.ndarray, draws: int, burn: int, proposal_scale: float, seed: int) -> tuple[np.ndarray, float]:", (
                "out = {{fn}}(np.array([1.0, 1.1, 0.9]), 20, 5, {{proposal}}, {{seed}})\nassert out[0].shape == (20,) and 0.0 <= out[1] <= 1.0",
                "out = {{fn}}(np.array([1.0]), 4, 0, 0.0, 3)\nassert np.allclose(out[0], out[0][0])",
                "out = {{fn}}(np.array([0.2, 0.4, 0.3]), 16, 4, {{proposal}}, 3)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return samples, acceptance_rate", "The proposal is symmetric in log space; acceptance compares the new and current log posteriors without exponentiating their difference prematurely."),
            _step("summary", "Compute posterior mean, quantile interval, effective sample size, and {{diagnostic_label}} from the retained chain.", "def {{fn}}(samples: np.ndarray, acceptance_rate: float) -> tuple[float, np.ndarray, float, float]:", (
                "samples = np.linspace(0.5, 1.5, {{draws}}); out = {{fn}}(samples, 0.3)\nassert out[1].shape == (2,) and out[2] > 0.0",
                "samples = np.ones(8); out = {{fn}}(samples, 1.0)\nassert np.allclose(out[0], 1.0) and out[2] == samples.size",
                "samples = np.array([0.8, 1.0, 1.2, 0.9, 1.1]); out = {{fn}}(samples, 0.4)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return posterior_mean, interval, effective_sample_size, diagnostic", "Effective sample size depends on the integrated autocorrelation of the retained chain, not merely on the acceptance rate."),
        ),
        (_mode("random_walk", "random-walk Metropolis", "Propose additive Gaussian moves in log-parameter space.", "A symmetric random walk makes the Hastings ratio a posterior ratio."), _mode("adaptive", "diminishing adaptation", "Adapt proposal scale during burn-in and freeze it before retained draws.", "Adaptation must stop before samples are used for stationary summaries.")),
        ("positive", "stochastic", "ensemble", "noisy"), ("uncertainty", "likelihood", "stability"),
        "emcee ensemble-sampling reference project", "https://github.com/dfm/emcee", ("statistics", "mcmc", "posterior"),
    ),
    _recipe(
        "graph_spectral", "statistics", "network-science",
        "Graph Laplacian embedding and spectral clustering",
        "Construct a weighted graph Laplacian, compute a low-dimensional spectral embedding, and evaluate cluster separation. {{scenario_description}} {{mode_description}}",
        "Use {{nodes}} nodes, target cluster count={{clusters}}, neighborhood parameter k={{k}}, and seed={{seed}} for synthetic graph tests. The adjacency matrix is symmetric and nonnegative.",
        "import numpy as np\nfrom scipy.linalg import eigh",
        "The graph Laplacian encodes local connectivity rather than Euclidean distance alone. Degree normalization, eigenvector ordering, and zero-eigenvalue multiplicity are part of the scientific definition. {{scenario_background}} {{mode_background}}",
        _p_graph,
        (
            _step("laplacian", "Construct the unnormalized or symmetric-normalized graph Laplacian from a symmetric weighted adjacency matrix and return degrees.", "def {{fn}}(adjacency: np.ndarray, normalized: bool) -> tuple[np.ndarray, np.ndarray]:", (
                "a = np.zeros(({{nodes}}, {{nodes}})); out = {{fn}}(a, False)\nassert out[0].shape == a.shape and np.allclose(out[1], 0.0)",
                "a = np.array([[0.0, 1.0], [1.0, 0.0]]); out = {{fn}}(a, True)\nassert np.allclose(np.diag(out[0]), 1.0)",
                "a = np.array([[0.0, 2.0, 0.0], [2.0, 0.0, 1.0], [0.0, 1.0, 0.0]]); out = {{fn}}(a, False)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return laplacian, degrees", "The unnormalized Laplacian is D-A. The symmetric normalized form is I-D to the minus one-half A D to the minus one-half, with an explicit zero-degree convention."),
            _step("embedding", "Compute the first nonconstant Laplacian eigenvectors and return the spectral embedding and eigenvalues. Reuse {{fn_laplacian}}.", "def {{fn}}(laplacian: np.ndarray, embedding_dimension: int) -> tuple[np.ndarray, np.ndarray]:", (
                "a = np.zeros(({{nodes}}, {{nodes}})); a[0, 1] = a[1, 0] = 1.0; l, _ = {{fn_laplacian}}(a, False); out = {{fn}}(l, 1)\nassert out[0].shape == ({{nodes}}, 1)",
                "l = np.zeros((3, 3)); out = {{fn}}(l, 2)\nassert out[0].shape == (3, 2)",
                "l = np.array([[1.0, -1.0], [-1.0, 1.0]]); out = {{fn}}(l, 1)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return embedding, eigenvalues", "The constant eigenvector is omitted for a connected graph. Eigenvector signs are arbitrary, so downstream comparisons must use sign-invariant quantities or a sign convention."),
            _step("clusters", "Assign embedding rows to clusters and return labels, within-cluster sum of squares, and {{diagnostic_label}}.", "def {{fn}}(embedding: np.ndarray, cluster_count: int, seed: int) -> tuple[np.ndarray, float, float]:", (
                "embedding = np.zeros(({{nodes}}, 2)); out = {{fn}}(embedding, {{clusters}}, {{seed}})\nassert out[0].shape == ({{nodes}},) and out[1] >= 0.0",
                "embedding = np.array([[0.0], [0.1], [5.0], [5.1]]); out = {{fn}}(embedding, 2, 7)\nassert len(np.unique(out[0])) == 2",
                "embedding = np.array([[0.0, 1.0], [1.0, 0.0], [3.0, 3.0]]); out = {{fn}}(embedding, 2, 3)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return labels, within_cluster_ss, diagnostic", "K-means initialization must be seeded. A spectral embedding can be sign-flipped without changing cluster assignments."),
        ),
        (_mode("unnormalized", "unnormalized Laplacian", "Use D-A and omit the constant eigenvector.", "The unnormalized spectrum reflects absolute degree as well as connectivity."), _mode("normalized", "symmetric normalized Laplacian", "Use the symmetric degree-normalized Laplacian.", "Normalization reduces degree-scale effects but requires a zero-degree convention.")),
        ("bounded", "sparse", "multichannel", "stochastic"), ("spectrum", "stability", "robustness"),
        "NetworkX graph spectral reference project", "https://github.com/networkx/networkx", ("statistics", "graph", "clustering"),
    ),
    _recipe(
        "wavelet_denoise", "signal", "time-frequency-analysis",
        "Wavelet thresholding for transient-signal denoising",
        "Compute a multilevel orthogonal wavelet transform, threshold detail coefficients, reconstruct the signal, and report scale-dependent SNR. {{scenario_description}} {{mode_description}}",
        "Use {{points}} samples, decomposition level={{level}}, noise scale={{noise}}, and threshold={{threshold}}. The signal length is compatible with the selected wavelet boundary convention.",
        "import numpy as np\nfrom scipy import signal",
        "Wavelet denoising separates transient structure by scale. Boundary extension, coefficient ordering, and threshold bias affect both reconstruction and scientific interpretation. {{scenario_background}} {{mode_background}}",
        _p_wavelet,
        (
            _step("transform", "Implement a multilevel Haar analysis transform and return approximation and detail coefficient arrays with their lengths.", "def {{fn}}(signal_values: np.ndarray, level: int) -> tuple[np.ndarray, list[np.ndarray]]:", (
                "x = np.sin(np.arange({{points}}) * 0.03); out = {{fn}}(x, {{level}})\nassert out[0].ndim == 1 and len(out[1]) == {{level}}",
                "x = np.ones(8); out = {{fn}}(x, 2)\nassert np.allclose(out[1][-1], 0.0)",
                "x = np.array([1.0, 2.0, 3.0, 4.0]); out = {{fn}}(x, 1)\nassert np.allclose(np.asarray([out[0], *out[1]]), np.asarray(target))",
            ), "return approximation, details", "The Haar transform uses pairwise averages and differences with a fixed normalization. Detail level one is the finest scale."),
            _step("threshold", "Apply the selected hard or soft threshold to detail coefficients and return thresholded coefficients without changing approximation coefficients.", "def {{fn}}(approximation: np.ndarray, details: list[np.ndarray], threshold: float, soft: bool) -> list[np.ndarray]:", (
                "a = np.array([1.0, 2.0]); d = [np.array([0.1, -0.8]), np.array([0.02, 0.5])]; out = {{fn}}(a, d, {{threshold}}, True)\nassert len(out) == len(d)",
                "a = np.array([1.0]); d = [np.array([0.0])]; out = {{fn}}(a, d, 0.0, False)\nassert np.allclose(out[0], d[0])",
                "a = np.array([0.2, 0.4]); d = [np.array([0.1, -0.3])]; out = {{fn}}(a, d, 0.2, False)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return thresholded_details", "Hard thresholding keeps coefficients above the cutoff; soft thresholding additionally shrinks their magnitude toward zero."),
            _step("reconstruct", "Reconstruct the denoised signal and return RMS error against a reference, retained coefficient fraction, and {{diagnostic_label}}.", "def {{fn}}(approximation: np.ndarray, details: list[np.ndarray], original: np.ndarray) -> tuple[np.ndarray, float, float, float]:", (
                "x = np.sin(np.arange({{points}}) * 0.03); a, d = {{fn_transform}}(x, {{level}}); out = {{fn}}(a, d, x)\nassert out[0].shape == x.shape and out[1] >= 0.0",
                "x = np.ones(8); a, d = {{fn_transform}}(x, 8, 2); out = {{fn}}(a, d, x)\nassert np.allclose(out[0], x)",
                "x = np.array([1.0, 2.0, 3.0, 4.0]); a, d = {{fn_transform}}(x, 1); out = {{fn}}(a, d, x)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return reconstructed, rms_error, retained_fraction, diagnostic", "Perfect reconstruction is expected before thresholding. The retained fraction is measured over detail coefficients, not over array bytes."),
        ),
        (_mode("hard", "hard thresholding", "Set detail coefficients below threshold to zero.", "Hard thresholding preserves large coefficients but introduces a discontinuity at the cutoff."), _mode("soft", "soft thresholding", "Shrink detail coefficients toward zero by the threshold.", "Soft thresholding is continuous but introduces bias in large coefficients.")),
        ("bounded", "noisy", "multichannel", "sparse"), ("robustness", "spectrum", "uncertainty"),
        "PyWavelets reference project", "https://github.com/PyWavelets/pywt", ("signal", "wavelet", "denoising"),
    ),
    _recipe(
        "ar_spectrum", "signal", "stochastic-processes",
        "Autoregressive spectrum and innovation diagnostics",
        "Estimate autoregressive coefficients from a time series, evaluate its power spectrum, and report innovation variance and peak frequency. {{scenario_description}} {{mode_description}}",
        "Use {{points}} samples, AR order={{order}}, noise scale={{noise}}, and {{frequency_bins}} frequency bins. The series is real-valued and the spectrum is one-sided.",
        "import numpy as np\nfrom scipy.linalg import toeplitz, solve",
        "An autoregressive model converts temporal dependence into a rational spectrum. Estimation convention, stability of the characteristic polynomial, and one-sided normalization must be explicit. {{scenario_background}} {{mode_background}}",
        _p_ar,
        (
            _step("coefficients", "Estimate AR coefficients from autocovariances using the selected Yule-Walker or least-squares convention and return innovation variance.", "def {{fn}}(observations: np.ndarray, order: int) -> tuple[np.ndarray, float]:", (
                "x = np.sin(np.arange({{points}}) * 0.1); out = {{fn}}(x, {{order}})\nassert out[0].shape == ({{order}},) and out[1] >= 0.0",
                "x = np.ones(8); out = {{fn}}(x, 2)\nassert np.all(np.isfinite(out[0]))",
                "x = np.array([0.2, -0.1, 0.7, 0.4, -0.3, 0.2]); out = {{fn}}(x, 2)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return coefficients, innovation_variance", "The sign convention for AR coefficients determines the characteristic polynomial; it must match spectrum evaluation."),
            _step("spectrum", "Evaluate the one-sided AR power spectrum from coefficients and innovation variance on a frequency grid. Reuse {{fn_coefficients}}.", "def {{fn}}(coefficients: np.ndarray, innovation_variance: float, frequencies: np.ndarray) -> np.ndarray:", (
                "a = np.zeros({{order}}); f = np.linspace(0.0, 0.5, {{frequency_bins}}); out = {{fn}}(a, 1.0, f)\nassert out.shape == f.shape and np.all(out >= 0.0)",
                "a = np.array([0.5]); f = np.array([0.0, 0.25, 0.5]); out = {{fn}}(a, 0.2, f)\nassert np.all(np.isfinite(out))",
                "a = np.linspace(0.1, 0.4, {{order}}); f = np.array([0.1, 0.2, 0.3]); out = {{fn}}(a, 0.5, f)\nassert np.allclose(out, target)",
            ), "return power_spectrum", "The transfer denominator is one plus the signed sum of coefficients times exp(-2 pi i f k); power is sigma squared over its squared magnitude."),
            _step("diagnostics", "Return AR coefficients, spectrum, peak frequency, and {{diagnostic_label}} for a supplied series.", "def {{fn}}(observations: np.ndarray, order: int, frequency_bins: int) -> tuple[np.ndarray, np.ndarray, float, float]:", (
                "x = np.sin(np.arange({{points}}) * 0.08); out = {{fn}}(x, {{order}}, {{frequency_bins}})\nassert out[0].shape == ({{order}},) and out[1].shape == ({{frequency_bins}},)",
                "x = np.ones(16); out = {{fn}}(x, 2, 8)\nassert np.all(np.isfinite(np.asarray(out)))",
                "x = np.array([0.2, -0.1, 0.7, 0.4, -0.3, 0.2, 0.1]); out = {{fn}}(x, 2, 8)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return coefficients, power_spectrum, peak_frequency, diagnostic", "A peak at zero or Nyquist can be real; peak extraction must define endpoint handling and tie-breaking."),
        ),
        (_mode("yule_walker", "Yule-Walker estimate", "Solve the Toeplitz autocovariance system.", "Yule-Walker imposes a stationary covariance structure."), _mode("least_squares", "least-squares estimate", "Fit lagged observations by ordinary least squares.", "Least squares is direct but must state how the initial order samples are handled.")),
        ("bounded", "noisy", "irregular", "multichannel"), ("spectrum", "stability", "likelihood"),
        "statsmodels autoregressive reference project", "https://github.com/statsmodels/statsmodels", ("signal", "autoregression", "spectrum"),
    ),
    _recipe(
        "robust_regression", "statistics", "robust-inference",
        "Robust regression with bootstrap uncertainty",
        "Fit a robust linear trend with an M-estimator, identify leverage-sensitive observations, and bootstrap a slope interval. {{scenario_description}} {{mode_description}}",
        "Use {{points}} observations, outlier fraction={{outlier_fraction}}, Huber transition={{delta}}, and {{bootstrap}} bootstrap replicates. Inputs are one predictor and one response.",
        "import numpy as np\nfrom scipy.optimize import minimize",
        "Robust regression changes the loss rather than deleting observations. The influence function, scale estimate, and bootstrap resampling unit must be explicit. {{scenario_background}} {{mode_background}}",
        _p_robust,
        (
            _step("loss", "Evaluate the Huber loss and its influence weight for residuals at transition delta.", "def {{fn}}(residuals: np.ndarray, delta: float) -> tuple[np.ndarray, np.ndarray]:", (
                "r = np.array([-2.0, -0.2, 0.0, 0.3, 3.0]); out = {{fn}}(r, {{delta}})\nassert out[0].shape == r.shape and out[1].shape == r.shape",
                "r = np.zeros({{points}}); out = {{fn}}(r, {{delta}})\nassert np.allclose(out, 0.0) or np.allclose(out[1], 1.0)",
                "r = np.array([-1.0, 0.5, 2.0]); out = {{fn}}(r, 1.0)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return loss, weights", "Huber loss is quadratic inside delta and linear outside; its IRLS weight is delta over absolute residual for outliers."),
            _step("fit", "Fit intercept and slope with iteratively reweighted least squares using {{fn_loss}} and return coefficients plus residual scale.", "def {{fn}}(predictor: np.ndarray, response: np.ndarray, delta: float) -> tuple[np.ndarray, float]:", (
                "x = np.linspace(0.0, 1.0, {{points}}); y = 2.0 + 3.0 * x; out = {{fn}}(x, y, {{delta}})\nassert out[0].shape == (2,) and np.allclose(out[0], [2.0, 3.0], atol=1e-5)",
                "x = np.array([0.0, 1.0]); y = np.array([1.0, 4.0]); out = {{fn}}(x, y, 1.0)\nassert np.all(np.isfinite(out[0]))",
                "x = np.array([0.0, 0.5, 1.0, 1.5]); y = np.array([0.1, 1.1, 2.1, 3.0]); out = {{fn}}(x, y, 1.0)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return coefficients, residual_scale", "IRLS must update weights from residuals and stop on coefficient change or a declared iteration cap."),
            _step("bootstrap", "Bootstrap observations, refit the robust trend, and return the coefficient estimate, slope interval, and {{diagnostic_label}}.", "def {{fn}}(predictor: np.ndarray, response: np.ndarray, delta: float, bootstrap: int, seed: int) -> tuple[np.ndarray, np.ndarray, float]:", (
                "x = np.linspace(0.0, 1.0, {{points}}); y = 1.0 + 2.0 * x; out = {{fn}}(x, y, {{delta}}, 8, 7)\nassert out[0].shape == (2,) and out[1].shape == (2,)",
                "x = np.array([0.0, 1.0, 2.0]); y = np.array([1.0, 2.0, 3.0]); out = {{fn}}(x, y, 1.0, 4, 7)\nassert np.all(np.isfinite(np.asarray(out)))",
                "x = np.array([0.0, 0.5, 1.0, 1.5]); y = np.array([0.1, 1.1, 2.1, 3.0]); out = {{fn}}(x, y, 1.0, {{bootstrap}}, 7)\nassert np.allclose(np.asarray(out), np.asarray(target))",
            ), "return coefficients, slope_interval, diagnostic", "Pairs of predictor and response values must be resampled together. Percentile intervals depend on the bootstrap replicate count and seed."),
        ),
        (_mode("huber", "Huber M-estimator", "Use Huber residual loss with IRLS weights.", "Huber loss preserves efficiency near the center while limiting outlier influence."), _mode("tukey", "Tukey bisquare", "Use a redescending Tukey bisquare influence function.", "Tukey loss can reject severe outliers but needs a robust scale initialization.")),
        ("noisy", "sparse", "irregular", "ensemble"), ("uncertainty", "robustness", "sensitivity"),
        "statsmodels robust-linear-model reference project", "https://github.com/statsmodels/statsmodels", ("statistics", "robustness", "bootstrap"),
    ),
)


def component_signature(metadata: Mapping[str, object]) -> str:
    """Return the normalized diversity signature stored in batch metadata."""

    return "|".join(
        str(metadata[key])
        for key in ("component", "method_mode", "scenario", "diagnostic", "step_count", "test_case_counts")
    )


def recipe_for(index: int, seed: int = 0) -> ProblemRecipe:
    """Select a recipe deterministically while balancing catalog entries."""

    # Keep the catalog schedule independent of the numeric seed.  This makes
    # two seeded batches comparable by domain while the parameter/scenario
    # stream still changes with ``seed``.
    del seed
    return RECIPES[index % len(RECIPES)]


def render_component_query(problem_id: str, index: int, seed: int = 0) -> tuple[dict[str, object], dict[str, object]]:
    """Render one complete SciCode query and its component metadata."""

    recipe = recipe_for(index, seed)
    return _render_recipe(recipe, problem_id, index, seed)


def catalog_summary() -> dict[str, object]:
    """Return an auditable summary without constructing any query."""

    domains: dict[str, int] = {}
    subdomains: dict[str, int] = {}
    for recipe in RECIPES:
        domains[recipe.domain] = domains.get(recipe.domain, 0) + 1
        subdomains[recipe.subdomain] = subdomains.get(recipe.subdomain, 0) + 1
    return {
        "catalog_version": "manual-scientific-components-v1",
        "recipe_count": len(RECIPES),
        "scenario_count": len(SCENARIOS),
        "method_mode_count": len({mode.key for recipe in RECIPES for mode in recipe.modes}),
        "diagnostic_count": len(DIAGNOSTICS),
        "domains": domains,
        "subdomains": subdomains,
        "model_calls": 0,
        "official_scicode_dataset_read": False,
    }


__all__ = [
    "DESIGN_VARIANTS",
    "DIAGNOSTICS",
    "RECIPES",
    "SCENARIOS",
    "Diagnostic",
    "MethodMode",
    "ProblemRecipe",
    "Provenance",
    "Scenario",
    "StepComponent",
    "catalog_summary",
    "component_signature",
    "recipe_for",
    "render_component_query",
]
