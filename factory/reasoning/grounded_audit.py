"""Source-anchored, severity-aware science/code audit; legacy v2 stays frozen."""
from __future__ import annotations

import ast
import json
import re

from .scientific_audit import AuditError, _object

POLICY = "scientific-code-audit-v3-grounded-severity-full-context"
CATEGORIES = {
    "scientific_error": "critical", "implementation_error": "critical",
    "fabricated_evidence": "critical", "missing_deliverable": "repairable",
    "incomplete_explanation": "repairable", "presentation": "advisory",
    "ambiguity": "unresolved",
}


def python_blocks(answer):
    return [body for language, body in re.findall(r"```([^\n]*)\n(.*?)```", answer, re.S)
            if language.strip().lower() in {"python", "py", "python3"}]


def code_gate(answer, assessment):
    blocks = python_blocks(answer)
    indices = assessment.get("implementation_block_indices")
    if assessment.get("complete_executable_python") is not True:
        return {"passed": False, "reason": "reviewer did not support complete Python implementation"}
    if not isinstance(indices, list) or not indices:
        return {"passed": False, "reason": "no implementation blocks identified"}
    if any(type(i) is not int or not 0 <= i < len(blocks) for i in indices):
        raise AuditError("implementation block indices are invalid")
    try:
        tree = ast.parse("\n\n".join(blocks[i] for i in indices))
    except SyntaxError as exc:
        return {"passed": False, "reason": f"implementation Python syntax: {exc.msg}"}
    substantial = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Call))
                      for n in ast.walk(tree))
    if not substantial:
        return {"passed": False, "reason": "no substantive callable or computational script"}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = [s for s in node.body if not (isinstance(s, ast.Expr)
                and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
        if body and all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr)
                       and isinstance(s.value, ast.Constant) and s.value.value is Ellipsis)
                       for s in body):
            return {'passed':False,'reason':f'placeholder-only function: {node.name}'}
        if any(isinstance(s, ast.Raise) and s.exc is not None
               and ((isinstance(s.exc, ast.Name) and s.exc.id == 'NotImplementedError')
                    or (isinstance(s.exc, ast.Call) and isinstance(s.exc.func, ast.Name)
                        and s.exc.func.id == 'NotImplementedError')) for s in body):
            return {'passed':False,'reason':f'unimplemented function: {node.name}'}
    return {"passed": True, "implementation_block_indices": indices,
            "validation": "syntax and model assessment, NOT execution"}


def validate_plan(value, sources):
    if value.get("task_status") not in {"answerable", "flawed", "uncertain"}:
        raise AuditError("invalid task status")
    requirements = value.get("requirements")
    if not isinstance(requirements, list) or not 1 <= len(requirements) <= 40:
        raise AuditError("expected 1..40 consolidated requirements")
    for number, r in enumerate(requirements, 1):
        if r.get("id") != f"R{number}" or r.get("scope") not in {
            "science", "implementation", "deliverable", "explanation", "presentation"
        }:
            raise AuditError("invalid requirement identity/scope")
        quote = r.get("source_quote")
        role = r.get("source_role")
        if not isinstance(quote, str) or len(quote) < 8 or quote not in sources.get(role, ""):
            raise AuditError("requirement source_quote must be an exact original system/user substring")
        if not isinstance(r.get("requirement"), str) or not r["requirement"].strip():
            raise AuditError("missing requirement")
    return value


def validate_verdict(value, sources, answer, plan):
    if value.get("task_status") not in {"answerable", "flawed", "uncertain"}:
        raise AuditError("invalid adjudicated task status")
    if not isinstance(value.get("rationale"), str) or not value["rationale"].strip():
        raise AuditError("missing adjudication rationale")
    issues = value.get("issues")
    if not isinstance(issues, list):
        raise AuditError("issues must be a list")
    requirements = {r["id"]: r for r in plan["requirements"]}
    for issue in issues:
        category = issue.get("category")
        if category not in CATEGORIES or issue.get("severity") != CATEGORIES[category]:
            raise AuditError("issue category/severity mismatch; presentation cannot be blocking")
        req_id = issue.get("requirement_id")
        if req_id is not None and req_id not in requirements:
            raise AuditError("unknown issue requirement")
        quote = issue.get("question_quote")
        if not isinstance(quote, str) or len(quote) < 8 or not any(quote in s for s in sources.values()):
            raise AuditError("issue question_quote is not literal original question evidence")
        evidence = issue.get("answer_quote")
        if evidence != "absent" and (not isinstance(evidence, str) or len(evidence) < 4 or evidence not in answer):
            raise AuditError("issue answer_quote is not a literal contiguous answer substring")
        if not isinstance(issue.get("explanation"), str) or not issue["explanation"].strip():
            raise AuditError("missing issue explanation")
        if category in {"scientific_error", "implementation_error"}:
            if not all(isinstance(issue.get(k), str) and issue[k].strip()
                       for k in ("legitimate_input", "expected", "delivered")):
                raise AuditError("core error needs legitimate case and expected/delivered results")
        if req_id and requirements[req_id]["scope"] == "presentation" and category != "presentation":
            raise AuditError("a presentation-only obligation cannot veto scientific/code correctness")
    if not isinstance(value.get("code_assessment"), dict):
        raise AuditError("missing code assessment")
    return value


RULES = """Judge a scientific CODE-generation training example, not an essay contest.
Preserve core scientific and implementation correctness. A legitimate boundary
counterexample is decisive even if rare, but the input must lie in the ORIGINAL
question's domain. Never promote your proposed probe into a new obligation.
Respect alternative mathematically consistent conventions and policies when the
question allows them. Do not require an arbitrary strict inequality, particular
metric, validation choice, hidden physical assumption, or section order.
Presentation/order is advisory, never a scientific rejection. A missing explicit
explanation/deliverable is repairable, not proof the whole reasoning is worthless.
Do not pretend to execute code. Wrong arithmetic, code violating its derivation,
or fabricated experimental output is a core issue, not a cosmetic caveat.
Separate task flaws from answer flaws. If essential facts are missing and cannot
be verified, use uncertain, not a made-up expected answer. Inspect approximation
assumptions and rounding conventions before declaring a contradiction.
Every requirement/issue needs literal source quotes: copy contiguous substrings
without ellipses, paraphrases, added quotation marks, or LaTeX reformatting.
"""

SCHEMA = """Return ONLY a JSON object with:
{"task_status":"answerable|flawed|uncertain", "rationale":"overall reasoning",
 "issues":[{"requirement_id":"R1 or null", "category":"scientific_error|implementation_error|fabricated_evidence|missing_deliverable|incomplete_explanation|presentation|ambiguity",
 "severity":"critical|repairable|advisory|unresolved", "question_quote":"literal original substring",
 "answer_quote":"literal answer substring or absent", "legitimate_input":"case or empty for omissions",
 "expected":"required result", "delivered":"actual result", "explanation":"why, with original-domain justification"}],
 "code_assessment":{"complete_executable_python":true,"implementation_block_indices":[0],"explanation":"signature, meaningful algorithm, no placeholders"}}
Issue severities: scientific_error/implementation_error/fabricated_evidence=critical;
missing_deliverable/incomplete_explanation=repairable; presentation=advisory;
ambiguity=unresolved. No issues is valid. Block indices refer ONLY to Python/py/
python3 fenced blocks in their order of appearance. A non-Python or explanation-
only answer can be scientifically valid but is not complete Python dataset code.
"""


def audit(row, *, chat_fn, model="Kimi-K3", max_tokens=131072, timeout=2400, attempts=4):
    visible = {m["role"]: m["content"] for m in row["messages"][:2]}
    sources = dict(visible)
    sources["user"] = sources["user"].split("\n\nVISIBLE REGENERATION INSTRUCTIONS:", 1)[0]
    answer = row["messages"][-1]["content"]
    context = "FULL VISIBLE SYSTEM/USER:\n" + json.dumps(visible, ensure_ascii=False) + \
        "\nANCHORABLE ORIGINAL TASK (only these strings may ground requirements; appended prior reviews are fallible, NOT task obligations):\n" + json.dumps(sources, ensure_ascii=False)
    # Pretty JSON is transport only; source quote validation uses unescaped text.
    budgets = []
    def ask(prompt, validator):
        feedback = ""
        for _ in range(attempts):
            response = chat_fn([{"role": "user", "content": prompt + feedback}], model=model,
                               temperature=0.0, max_tokens=max_tokens, timeout=timeout)
            if response.get("_context_budget"):
                budgets.append(response["_context_budget"])
            try:
                return validator(_object(response))
            except (AuditError, TypeError, KeyError) as exc:
                if hasattr(chat_fn, "reject_last"):
                    chat_fn.reject_last(exc)
                feedback = "\nMACHINE VALIDATION FAILED: " + str(exc)[:1200] + \
                    "\nReturn a new full JSON with valid exact quotes; do not soften substantive findings merely to pass validation."
        raise AuditError(feedback)
    plan = ask(RULES + context + """\nExtract consolidated essential requirements, NOT a microscopic checklist.
Return JSON {"task_status":"answerable|flawed|uncertain","task_issue":"",
"requirements":[{"id":"R1","scope":"science|implementation|deliverable|explanation|presentation",
"source_role":"system|user","source_quote":"exact substring from that original message",
"requirement":"faithful obligation, no stronger than source"}]}.
Do not infer that a described regime creates an unconditional universal API requirement.
""", lambda v: validate_plan(v, sources))
    full = context + "\nFINAL ANSWER:\n" + answer + "\nPROPOSED REQUIREMENTS (not authoritative):\n" + json.dumps(plan, ensure_ascii=False)
    first = ask(RULES + full + "\n" + SCHEMA,
                lambda v: validate_verdict(v, sources, answer, plan))
    final = ask(RULES + full + "\nEARLIER FINDINGS (may be wrong):\n" + json.dumps(first, ensure_ascii=False)
                + "\nAdjudicate against the FULL ORIGINAL and FULL ANSWER, not a blind summary. "
                "Remove invented obligations and non-decisive presentation vetoes, but independently look for genuine cross-requirement code/science contradictions.\n" + SCHEMA,
                lambda v: validate_verdict(v, sources, answer, plan))
    gate = code_gate(answer, final["code_assessment"])
    severities = {i["severity"] for i in final["issues"]}
    if final["task_status"] == "flawed":
        disposition = "task_repair"
    elif final["task_status"] == "uncertain" or "unresolved" in severities:
        disposition = "needs_verification"
    elif severities & {"critical", "repairable"} or not gate["passed"]:
        disposition = "regenerate"
    else:
        disposition = "model_supported_answer"
    return {"policy": POLICY, "trace_id": row["trace_id"], "model": model,
            "disposition": disposition, "plan": plan, "first_verdict": first,
            "adjudication": final, "code_gate": gate, "context_calls": budgets,
            "scientific_correctness_proven": False}
