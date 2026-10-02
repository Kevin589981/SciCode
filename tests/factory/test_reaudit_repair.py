import hashlib
import json
from pathlib import Path

import pytest

from factory.reasoning.distill import run as old_run, export, select_reviewed, REASONING_REVIEW_POLICY
from factory.reasoning.prepare_reused_tasks import rows
from factory.reasoning.reaudit_repair import prepare, run
from factory.reasoning.scientific_audit import POLICY as OLD_AUDIT
from tests.factory.test_distill import inputs, config, response
from tests.factory_fixtures import grade_for


def legacy(root, answer):
    inp,_=inputs(root)
    source=root/'legacy'; source.mkdir()
    (source/'inputs').symlink_to(inp,target_is_directory=True)
    old_run(inp,source/'solver',config(),chat_fn=lambda *_a,**_k:response(answer=answer,reasoning='A scientifically productive derivation of the requested algorithm.'))
    report=export(inp,source/'solver',source/'native-v1')
    row=list(rows(source/'native-v1/audit-candidates.jsonl'))[0]
    h=hashlib.sha256(json.dumps(row,ensure_ascii=False).encode()).hexdigest()
    audit={'trace_id':row['trace_id'],'model':'Kimi-K3','policy':OLD_AUDIT,'row_sha256':h,'disposition':'quarantine'}
    (source/'scientific-audit.jsonl').write_text(json.dumps(audit)+'\n')
    g=grade_for(); g['message_annotations'][0]['train_content']=True
    g['reasoning_evidence']={'useful_quote':'A scientifically productive derivation','redundant_quote':'',
                                          'efficiency':'productive_but_long','explanation':'coherent'}
    grade={'trace_id':row['trace_id'],'model':'Kimi-K3','policy':REASONING_REVIEW_POLICY,'row_sha256':h,'grade':g}
    (source/'reasoning-quality.jsonl').write_text(json.dumps(grade)+'\n')
    select=select_reviewed(source/'native-v1',source/'scientific-audit.jsonl',source/'reviewed-v1','Kimi-K3',grades_path=source/'reasoning-quality.jsonl')
    (source/'pipeline_report.json').write_text(json.dumps({'export':report,'selection':select,'latest_sft':str(source/'reviewed-v1/sft.jsonl')}))
    return source,row,g


def reviewer_fn():
    def chat(messages,**kwargs):
        text=messages[0]['content']
        if 'TRAINING VALUE' in text:
            g=grade_for(); g['message_annotations'][0]['train_content']=True
            g['reasoning_evidence']={'useful_quote':'A scientifically productive derivation of','redundant_quote':'',
                                                  'efficiency':'efficient','explanation':'coherent'}
            return response(answer=json.dumps(g))
        marker='ANCHORABLE ORIGINAL TASK (only these strings may ground requirements; appended prior reviews are fallible, NOT task obligations):\n'
        source=json.JSONDecoder().raw_decode(text.split(marker,1)[1])[0]
        if 'Extract consolidated' in text:
            p={'task_status':'answerable','task_issue':'','requirements':[{'id':'R1','scope':'implementation','source_role':'user',
                                                                       'source_quote':source['user'][:80],'requirement':'implement algorithm'}]}
            return response(answer=json.dumps(p))
        answer=text.split('FINAL ANSWER:\n',1)[1].split('\nPROPOSED REQUIREMENTS',1)[0]
        v={'task_status':'answerable','rationale':'faithful to task','issues':[],
           'code_assessment':{'complete_executable_python':'```python' in answer,'implementation_block_indices':[0] if '```python' in answer else []}}
        return response(answer=json.dumps(v))
    return chat


@pytest.mark.parametrize('needs_generation',[False,True])
def test_live_reaudit_promotes_or_regenerates_and_preserves_original(tmp_path,needs_generation,monkeypatch):
    monkeypatch.setenv('SCICODE_LLM_API_KEY','test-key')
    code='```python\ndef solve(x):\n    return x*x\n```'
    source,row,g=legacy(tmp_path,'explanation without code' if needs_generation else code)
    before=hashlib.sha256((source/'native-v1/sft.jsonl').read_bytes()).hexdigest()
    prepared=tmp_path/'repair-inputs'; m=prepare([source],prepared); assert m['selected']==1
    cfg=config(); cfg['request_options']={'thinking':{'type':'enabled'}}
    calls=[]
    def teacher(messages,**kwargs):
        calls.append(messages)
        assert 'VISIBLE REGENERATION INSTRUCTIONS' in messages[1]['content']
        return response(answer=code,reasoning='A scientifically productive derivation of the requested algorithm.')
    result=run(prepared,tmp_path/'new-output',cfg,reviewer={'model':'Kimi-K3','base_url':'http://example.test/v1','api_key':'test'},
               review_workers=2,generation_workers=2,teacher_fn=teacher,review_fn=reviewer_fn())
    category='accepted_regenerated' if needs_generation else 'accepted_original'
    assert result['outcomes']=={category:1}
    assert len(calls)==int(needs_generation)
    sft=list(rows(Path(result['release'])/'sft.jsonl'))
    assert len(sft)==1 and '</think>' in sft[0]['messages'][-1]['content']
    assert '```python' in sft[0]['messages'][-1]['content']
    assert hashlib.sha256((source/'native-v1/sft.jsonl').read_bytes()).hexdigest()==before
    resumed=run(prepared,tmp_path/'new-output',cfg,reviewer={'model':'Kimi-K3','base_url':'http://example.test/v1','api_key':'test'},
                review_workers=2,generation_workers=2,teacher_fn=lambda *_a,**_k:pytest.fail('no repeat teacher'),review_fn=lambda *_a,**_k:pytest.fail('no repeat review'))
    assert resumed['outcomes']=={category:1}
