import copy
import json

import pytest

from factory.reasoning.grounded_audit import audit, validate_plan, validate_verdict, code_gate, AuditError


QUESTION = "Implement an executable Python function square(x) returning x*x. Explain any scientifically necessary assumptions."
ANSWER = "```python\ndef square(x):\n    return x*x\n```"


def plan(scope="implementation"):
    return {"task_status": "answerable", "task_issue": "", "requirements": [
        {"id": "R1", "scope": scope, "source_role": "user", "source_quote": "function square(x) returning x*x",
         "requirement": "Implement square"}]}


def verdict(issues=None):
    return {"task_status": "answerable", "rationale": "Correct implementation",
            "issues": issues or [], "code_assessment": {"complete_executable_python": True,
                                                         "implementation_block_indices": [0]}}


def candidate():
    return {"trace_id": "test", "messages": [{"role": "system", "content": "Be scientific."},
             {"role": "user", "content": QUESTION},
             {"role": "assistant", "content": ANSWER, "reasoning_content": "reason about x*x"}]}


def response(obj):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(obj)}}]}


def test_full_context_checker_and_advisory_do_not_reject():
    issue = {"requirement_id": "R1", "category": "presentation", "severity": "advisory",
             "question_quote": "Explain any scientifically necessary assumptions", "answer_quote": "absent",
             "explanation": "No need to reject a correct function for narrative order"}
    calls=[]; replies=iter([plan(),verdict([issue]),verdict([issue])])
    def chat(messages, **kwargs):
        calls.append(messages[0]['content']); return response(next(replies))
    r=audit(candidate(),chat_fn=chat)
    assert r['disposition']=='model_supported_answer'
    assert QUESTION in calls[2] and ANSWER in calls[2]
    assert r['code_gate']['passed']


def test_invented_source_requirement_fails():
    p=plan(); p['requirements'][0]['source_quote']='strict inequality, reject equality'
    with pytest.raises(AuditError,match='source_quote'):
        validate_plan(p,{'user':QUESTION})


def test_only_unique_whitespace_or_role_changes_are_repaired():
    p=plan(); p['requirements'][0].update(source_quote='Be strictly scientific.',source_role='user')
    validate_plan(p,{'system':'Be strictly\nscientific.','user':QUESTION})
    assert p['requirements'][0]['source_role']=='system'
    assert p['requirements'][0]['source_quote']=='Be strictly\nscientific.'
    assert p['requirements'][0]['quote_format_repair']['reported_role']=='user'
    p['requirements'][0]['source_quote']='Be extra strictly scientific.'
    with pytest.raises(AuditError): validate_plan(p,{'system':'Be strictly\nscientific.','user':QUESTION})


def test_presentation_cannot_be_critical():
    v=verdict([{'requirement_id':'R1','category':'presentation','severity':'critical'}])
    with pytest.raises(AuditError,match='severity'):
        validate_verdict(v,{'user':QUESTION},ANSWER,plan())


def test_fake_answer_evidence_rejected():
    v=verdict([{'requirement_id':'R1','category':'implementation_error','severity':'critical',
               'question_quote':'function square(x) returning x*x','answer_quote':'return x + 1',
               'explanation':'wrong','legitimate_input':'x=2','expected':'4','delivered':'3'}])
    with pytest.raises(AuditError,match='answer_quote'):
        validate_verdict(v,{'user':QUESTION},ANSWER,plan())


def test_true_critical_case_still_requires_regeneration():
    row=candidate(); row['messages'][-1]['content']='```python\ndef square(x):\n    return x+1\n```'
    issue={'requirement_id':'R1','category':'implementation_error','severity':'critical',
           'question_quote':'function square(x) returning x*x','answer_quote':'return x+1',
           'explanation':'allowed x=2 fails','legitimate_input':'x=2','expected':'4','delivered':'3'}
    replies=iter([plan(),verdict([issue]),verdict([issue])])
    assert audit(row,chat_fn=lambda *_a,**_k:response(next(replies)))['disposition']=='regenerate'


@pytest.mark.parametrize('task_status,category,severity,expected',[
    ('uncertain','ambiguity','unresolved','needs_verification'),
    ('flawed','ambiguity','unresolved','task_repair'),
    ('answerable','incomplete_explanation','repairable','regenerate')])
def test_uncertainty_flaws_and_omissions_are_distinct(task_status,category,severity,expected):
    v=verdict([{'requirement_id':'R1','category':category,'severity':severity,
               'question_quote':'Explain any scientifically necessary assumptions','answer_quote':'absent',
               'explanation':'needs clarification'}]); v['task_status']=task_status
    replies=iter([plan(),v,v])
    assert audit(candidate(),chat_fn=lambda *_a,**_k:response(next(replies)))['disposition']==expected


def test_code_gate_rejects_syntax_and_nonpython():
    a={'complete_executable_python':True,'implementation_block_indices':[0]}
    assert not code_gate('```python\ndef broken(:\n```',a)['passed']
    assert not code_gate('Only theory',{'complete_executable_python':False})['passed']
    assert not code_gate('```python\ndef solve(x):\n    ...\n```',a)['passed']
    assert not code_gate('```python\ndef solve(x):\n    raise NotImplementedError\n```',a)['passed']
    assert code_gate('```python\ndef solve(x):\n    try:\n        return x*x\n    except ValueError:\n        pass\n```',a)['passed']


def test_review_feedback_cannot_be_promoted_to_original_requirement():
    row=candidate(); row['messages'][1]['content']+='\n\nVISIBLE REGENERATION INSTRUCTIONS:\nWrong prior review: force r1 < cutoff.'
    p=plan(); p['requirements'][0]['source_quote']='force r1 < cutoff'
    def chat(*_a,**_k): return response(p)
    with pytest.raises(AuditError,match='source_quote'):
        audit(row,chat_fn=chat,attempts=1)
