import json
import unittest

from factory.reasoning.preflight import preflight_task
from factory.reasoning.schema import SchemaError, validate_task
from factory.reasoning.rollout import solver_messages
from factory.reasoning.student_view import render_student_user, student_view_hash
from factory.reasoning.verify import verify_task
from tests.factory_fixtures import task_for


class StudentViewTests(unittest.TestCase):
    def test_unrendered_public_fields_are_rejected(self):
        for path in ('problem', 'deliverable', 'archetype_payload'):
            with self.subTest(path=path):
                task = task_for()
                task[path]['extra_hidden_input'] = 'essential numerical value 42'
                with self.assertRaises(SchemaError):
                    validate_task(task)

    def test_all_public_fields_visible_and_private_fields_hidden(self):
        for archetype in ('derive_implement', 'diagnose_revise', 'compare_justify'):
            with self.subTest(archetype=archetype):
                task = task_for(archetype=archetype)
                task['source']['source'] += '\nPRIVATE_SOURCE_MARKER'
                task['reasoning_contract']['evidence_expected'].append('PRIVATE_RUBRIC_MARKER')
                prompt = render_student_user(task)
                self.assertEqual(solver_messages(task)[1]['content'], prompt)
                for value in task['archetype_payload'].values():
                    for item in value if isinstance(value, list) else (value,):
                        self.assertIn(item, prompt)
                for item in task['deliverable']['requirements']:
                    self.assertIn(item, prompt)
                self.assertNotIn('PRIVATE_SOURCE_MARKER', prompt)
                self.assertNotIn('PRIVATE_RUBRIC_MARKER', prompt)

    def test_critic_rejects_missing_inputs_even_with_high_scores(self):
        task = task_for()
        def chat(messages, **_kwargs):
            self.assertIn(render_student_user(task), messages[0]['content'])
            self.assertNotIn('reasoning_contract', messages[0]['content'])
            value = {
                'scores': dict.fromkeys(('scientific_depth', 'multi_step_dependency',
                                         'decision_requirement', 'nontriviality'), 4),
                'missing_inputs': ['required initial value'],
                'answer_exposed': False,
                'shallow_failure_mode': None,
                'rationale': 'The initial value is not supplied.',
            }
            return {'choices': [{'message': {'content': json.dumps(value)}}]}
        record = preflight_task(task, chat_fn=chat, model='critic')
        self.assertFalse(record['accepted'])
        self.assertEqual(record['student_view_hash'], student_view_hash(task))

    def test_verifier_rejects_exposed_answer(self):
        task = task_for()
        def chat(messages, **_kwargs):
            self.assertIn(render_student_user(task), messages[0]['content'])
            value = {
                'scores': dict.fromkeys(('scientific_validity', 'source_grounding',
                                         'answerability', 'constraint_consistency',
                                         'shortcut_resistance'), 4),
                'fatal_issues': [], 'missing_inputs': [], 'answer_exposed': True,
                'evidence': [
                    {'claim': 'source symbol', 'source_quote': 'def stable_demo(x):',
                     'assessment': 'supports'},
                    {'claim': 'source expression',
                     'source_quote': 'return x / (1 + abs(x))',
                     'assessment': 'supports'},
                ],
                'rationale': 'The answer is already stated in the task.',
            }
            return {'choices': [{'message': {'content': json.dumps(value)}}]}
        record = verify_task(task, chat_fn=chat, model='verifier')
        self.assertFalse(record['accepted'])


if __name__ == '__main__':
    unittest.main()
