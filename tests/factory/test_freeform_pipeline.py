import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.reasoning.freeform import (
    AUDIT_POLICY,
    AUDIT_SCHEMA,
    FreeformError,
    SFT_SCHEMA,
    TASK_SCHEMA,
    author_messages,
    audit_messages,
    audit_trace,
    collect_trace,
    compose_task,
    qwen_messages,
    sample_parameters,
    training_projection,
    training_row,
    validate_task,
)
from factory.reasoning.freeform_batch import (
    RECIPE_SCHEMA,
    aggregate,
    enqueue_seeds,
    prepare_seeds,
    run_workers,
)
from factory.reasoning.queue import WorkQueue


SEED = {
    "schema_version": "scicode-freeform-seed-v1",
    "seed_id": "source-1",
    "source": {
        "repo_id": "scicodepile:example/science",
        "repo": "example/science",
        "snapshot_hash": "sha-1",
        "file": "science/model.py",
        "symbol": "equilibrium",
        "excerpt": "def equilibrium(x): return x * x",
    },
}
QUESTION = (
    "一个简化的无量纲平衡模型为 E(x)=x²，其中 x 为非负浓度。"
    "推导浓度按因子 f 缩放时 E 的变化，并用任一编程语言实现"
    "计算缩放后平衡量的函数，说明输入条件。"
)


def response(content, reasoning="", finish="stop"):
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": content,
                "reasoning_content": reasoning,
            },
            "finish_reason": finish,
        }],
        "usage": {"completion_tokens": 42},
    }


def fake_chat(messages, **kwargs):
    model = kwargs["model"]
    if model == "kimi-author":
        return response(json.dumps({
            "question": QUESTION,
            "reference_answer": (
                "E(fx)=f²E(x)。Python 实现：\n"
                "def scaled_equilibrium(x, f):\n    return (f * x) ** 2"
            ),
        }, ensure_ascii=False))
    if model == "qwen-base":
        if messages != [{"role": "user", "content": QUESTION}]:
            raise AssertionError("Qwen received an altered question")
        return response(
            "E(fx)=f²E(x)，要求 x、f 非负。\n\n"
            "```python\ndef scaled_equilibrium(x, f):\n"
            "    return (f * x) ** 2\n```",
            reasoning="由 E(x)=x²，得 E(fx)=(fx)²=f²E(x)。函数直接计算缩放后的浓度平方。",
        )
    if model == "kimi-audit":
        return response(json.dumps({
            "key_check": "题面给定 E(x)=x²；回答推得 E(fx)=f²E(x)。",
            "coding_verdict": "accept",
            "verdict": "accept",
            "reason": "推导正确，最终代码实现了缩放后的平衡量。",
        }, ensure_ascii=False))
    raise AssertionError(model)


class FreeformPipelineTests(unittest.TestCase):
    def test_author_asks_for_scientific_coding_without_fixed_archetype(self):
        prompts = [author_messages(SEED, variant)[0]["content"]
                   for variant in range(3)]
        self.assertEqual(len(set(prompts)), 3)
        for prompt in prompts:
            self.assertIn("代码交付", prompt)
            self.assertIn("方法与实现细节留给解题者选择", prompt)
            self.assertIn("内部核查", prompt)
            self.assertIn(SEED["source"]["excerpt"], prompt)
            self.assertNotIn("archetype_payload", prompt)

    def test_qwen_gets_only_authored_question_and_native_thinking(self):
        task = compose_task(
            SEED, chat_fn=fake_chat, model="kimi-author",
            base_url="http://kimi/v1", api_key="dummy",
            temperature=0.9, top_p=0.95, request_seed=None,
            max_tokens=1000, timeout=20,
        )
        self.assertEqual(task["schema_version"], TASK_SCHEMA)
        self.assertEqual(qwen_messages(task), [{"role": "user", "content": QUESTION}])
        samples = [sample_parameters(task["task_id"], i, 17) for i in range(4)]
        self.assertEqual(samples[0], sample_parameters(task["task_id"], 0, 17))
        self.assertEqual(len({s["request_seed"] for s in samples}), 4)
        trace = collect_trace(
            task, attempt=0, sample=samples[0], chat_fn=fake_chat,
            model="qwen-base", base_url="http://qwen/v1", api_key="dummy",
            send_seed=False, max_tokens=1000, timeout=20,
        )
        self.assertEqual(trace["reasoning_content"],
                         "由 E(x)=x²，得 E(fx)=(fx)²=f²E(x)。函数直接计算缩放后的浓度平方。")
        self.assertEqual(trace["prompt"], qwen_messages(task))
        review_prompt = audit_messages(task, trace)[0]["content"]
        self.assertIn("最终回答作为一个交付物整体判断", review_prompt)
        self.assertIn("核心接口、输入输出或功能", review_prompt)
        audit = audit_trace(
            task, trace, chat_fn=fake_chat, model="kimi-audit",
            base_url="http://kimi/v1", api_key="dummy",
            max_tokens=1000, timeout=20,
        )
        self.assertEqual(audit["schema_version"], AUDIT_SCHEMA)
        self.assertEqual(audit["policy_version"], AUDIT_POLICY)
        self.assertEqual(audit["coding_verdict"], "accept")
        row = training_row(task, trace, audit)
        self.assertEqual(row["schema_version"], SFT_SCHEMA)
        self.assertEqual(row["audit"]["key_check"], audit["key_check"])
        self.assertEqual(row["audit"]["coding_verdict"], "accept")
        self.assertEqual(row["messages"][1]["reasoning_content"],
                         trace["reasoning_content"])
        projected = training_projection(row)
        self.assertEqual(projected["messages"][0]["content"], QUESTION)
        self.assertIn("<think>\n由 E(x)", projected["messages"][1]["content"])
        self.assertTrue(projected["messages"][1]["content"].endswith(trace["content"]))
        self.assertEqual(projected["metadata"]["coding_verdict"], "accept")

    def test_conceptual_qa_is_not_exported_even_if_scientifically_correct(self):
        def conceptual_author(messages, **kwargs):
            return response(json.dumps({
                "question": "为什么平方律下浓度加倍会使平衡量变为四倍？",
                "reference_answer": "因为 (2x)²=4x²。",
            }, ensure_ascii=False))

        task = compose_task(
            SEED, chat_fn=conceptual_author, model="kimi-author",
            base_url="http://kimi/v1", api_key="dummy",
            temperature=0.9, top_p=0.95, request_seed=None,
            max_tokens=1000, timeout=20,
        )
        trace = collect_trace(
            task, attempt=0, sample=sample_parameters(task["task_id"], 0, 1),
            chat_fn=lambda messages, **kwargs: response(
                "因为 (2x)²=4x²。", reasoning="由平方律直接推导。"),
            model="qwen-base", base_url="http://qwen/v1", api_key="dummy",
            send_seed=False, max_tokens=1000, timeout=20,
        )

        def conceptual_audit(messages, **kwargs):
            return response(json.dumps({
                "key_check": "平方律推导正确。",
                "coding_verdict": "reject",
                "verdict": "accept",
                "reason": "科学解释正确，但题目未要求代码，答案也没有代码。",
            }, ensure_ascii=False))

        audit = audit_trace(
            task, trace, chat_fn=conceptual_audit, model="kimi-audit",
            base_url="http://kimi/v1", api_key="dummy",
            max_tokens=1000, timeout=20,
        )
        self.assertEqual(audit["verdict"], "accept")
        self.assertEqual(audit["coding_verdict"], "reject")
        self.assertIsNone(training_row(task, trace, audit))

    def test_non_python_code_can_be_exported(self):
        task = compose_task(
            SEED, chat_fn=fake_chat, model="kimi-author",
            base_url="http://kimi/v1", api_key="dummy",
            temperature=0.9, top_p=0.95, request_seed=None,
            max_tokens=1000, timeout=20,
        )
        answer = "E(fx)=f²E(x)。\n```julia\nscaled_equilibrium(x, f) = (f*x)^2\n```"
        trace = collect_trace(
            task, attempt=0, sample=sample_parameters(task["task_id"], 0, 1),
            chat_fn=lambda messages, **kwargs: response(
                answer, reasoning="将 f 乘入 x 后按平方律计算。"),
            model="qwen-base", base_url="http://qwen/v1", api_key="dummy",
            send_seed=False, max_tokens=1000, timeout=20,
        )
        audit = audit_trace(
            task, trace, chat_fn=fake_chat, model="kimi-audit",
            base_url="http://kimi/v1", api_key="dummy",
            max_tokens=1000, timeout=20,
        )
        self.assertEqual(training_row(task, trace, audit)["messages"][1]["content"],
                         answer)

    def test_raw_incomplete_trace_is_kept_but_not_exported(self):
        task = compose_task(
            SEED, chat_fn=fake_chat, model="kimi-author",
            base_url="http://kimi/v1", api_key="dummy",
            temperature=0.9, top_p=0.95, request_seed=None,
            max_tokens=1000, timeout=20,
        )

        def incomplete(messages, **kwargs):
            return response("", reasoning="继续推理", finish="length")

        trace = collect_trace(
            task, attempt=0, sample=sample_parameters(task["task_id"], 0, 1),
            chat_fn=incomplete, model="qwen-base",
            base_url="http://qwen/v1", api_key="dummy",
            send_seed=False, max_tokens=100, timeout=20,
        )
        audit = audit_trace(
            task, trace, chat_fn=fake_chat, model="kimi-audit",
            base_url="http://kimi/v1", api_key="dummy",
            max_tokens=1000, timeout=20,
        )
        self.assertEqual(trace["reasoning_content"], "继续推理")
        self.assertIsNone(training_row(task, trace, audit))

    def test_invalid_audit_verdict_is_quarantined_with_raw_response(self):
        task = compose_task(
            SEED, chat_fn=fake_chat, model="kimi-author",
            base_url="http://kimi/v1", api_key="dummy",
            temperature=0.9, top_p=0.95, request_seed=None,
            max_tokens=1000, timeout=20,
        )
        trace = collect_trace(
            task, attempt=0, sample=sample_parameters(task["task_id"], 0, 1),
            chat_fn=fake_chat, model="qwen-base",
            base_url="http://qwen/v1", api_key="dummy",
            send_seed=False, max_tokens=1000, timeout=20,
        )

        def invalid_review(messages, **kwargs):
            return response(json.dumps({
                "key_check": "已核对题目与回答。",
                "coding_verdict": "accept",
                "verdict": "mostly_accept",
                "reason": "判定值不在协议内。",
            }, ensure_ascii=False))

        audit = audit_trace(
            task, trace, chat_fn=invalid_review, model="kimi-audit",
            base_url="http://kimi/v1", api_key="dummy",
            max_tokens=1000, timeout=20,
        )
        self.assertEqual(audit["verdict"], "uncertain")
        self.assertIn("mostly_accept", audit["reason"])
        self.assertIsNone(training_row(task, trace, audit))

    def test_missing_coding_verdict_is_quarantined(self):
        task = compose_task(
            SEED, chat_fn=fake_chat, model="kimi-author",
            base_url="http://kimi/v1", api_key="dummy",
            temperature=0.9, top_p=0.95, request_seed=None,
            max_tokens=1000, timeout=20,
        )
        trace = collect_trace(
            task, attempt=0, sample=sample_parameters(task["task_id"], 0, 1),
            chat_fn=fake_chat, model="qwen-base",
            base_url="http://qwen/v1", api_key="dummy",
            send_seed=False, max_tokens=1000, timeout=20,
        )

        def old_format_review(messages, **kwargs):
            return response(json.dumps({
                "key_check": "科学关系正确。",
                "verdict": "accept",
                "reason": "旧版审核缺少编程判断。",
            }, ensure_ascii=False))

        audit = audit_trace(
            task, trace, chat_fn=old_format_review, model="kimi-audit",
            base_url="http://kimi/v1", api_key="dummy",
            max_tokens=1000, timeout=20,
        )
        self.assertEqual(audit["verdict"], "uncertain")
        self.assertEqual(audit["coding_verdict"], "uncertain")
        self.assertIsNone(training_row(task, trace, audit))

    def test_author_rejects_truncated_reasoning_draft(self):
        draft = json.dumps({
            "question": "题目：科学编程任务\n...",
            "reference_answer": "参考代码：...",
        }, ensure_ascii=False)

        def truncated_author(messages, **kwargs):
            return response("", reasoning=draft, finish="length")

        with self.assertRaisesRegex(FreeformError, "did not finish"):
            compose_task(
                SEED, chat_fn=truncated_author, model="kimi-author",
                base_url="http://kimi/v1", api_key="dummy",
                temperature=0.9, top_p=0.95, request_seed=None,
                max_tokens=1000, timeout=20,
            )
        task = compose_task(
            SEED, chat_fn=fake_chat, model="kimi-author",
            base_url="http://kimi/v1", api_key="dummy",
            temperature=0.9, top_p=0.95, request_seed=None,
            max_tokens=1000, timeout=20,
        )
        task["authoring"]["finish_reason"] = "length"
        task["authoring"]["response_field"] = "reasoning_content"
        with self.assertRaisesRegex(FreeformError, "complete author final answer"):
            validate_task(task)

    def test_audit_does_not_accept_json_drafts_or_truncated_output(self):
        task = compose_task(
            SEED, chat_fn=fake_chat, model="kimi-author",
            base_url="http://kimi/v1", api_key="dummy",
            temperature=0.9, top_p=0.95, request_seed=None,
            max_tokens=1000, timeout=20,
        )
        trace = collect_trace(
            task, attempt=0, sample=sample_parameters(task["task_id"], 0, 1),
            chat_fn=fake_chat, model="qwen-base",
            base_url="http://qwen/v1", api_key="dummy",
            send_seed=False, max_tokens=1000, timeout=20,
        )
        verdict = json.dumps({
            "key_check": "正确。", "coding_verdict": "accept",
            "verdict": "accept", "reason": "科学与代码都正确。",
        }, ensure_ascii=False)
        for content, reasoning, finish in (("", verdict, "stop"),
                                           (verdict, "", "length")):
            with self.subTest(finish=finish, content=bool(content)):
                audit = audit_trace(
                    task, trace,
                    chat_fn=lambda messages, **kwargs: response(
                        content, reasoning=reasoning, finish=finish),
                    model="kimi-audit", base_url="http://kimi/v1",
                    api_key="dummy", max_tokens=1000, timeout=20,
                )
                self.assertEqual(audit["verdict"], "uncertain")
                self.assertEqual(audit["coding_verdict"], "uncertain")
                self.assertIsNone(training_row(task, trace, audit))

    def test_prepared_catalog_queue_resume_and_aggregate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            (snapshot / "model.py").write_text(
                "import math\n\n"
                "def equilibrium(x):\n"
                "    \"\"\"Compute a scientific equilibrium observable.\"\"\"\n"
                "    y = math.exp(-x)\n"
                "    z = 1.0 + y\n"
                "    a = z * z\n"
                "    b = a - y\n"
                "    return b\n",
                encoding="utf-8",
            )
            (snapshot / "thirdParty").mkdir()
            (snapshot / "thirdParty" / "relicense.py").write_text(
                "print('not science')\n", encoding="utf-8")
            (snapshot / ".scicodepile_snapshot.json").write_text(
                json.dumps({"snapshot_hash": "sha-1"}), encoding="utf-8")
            catalog = root / "catalog.jsonl"
            catalog.write_text(json.dumps({
                "source_kind": "scicodepile_clean_dataset",
                "repo_id": "scicodepile:example/science",
                "full_name": "example/science",
                "snapshot_path": str(snapshot),
                "snapshot_hash": "sha-1",
                "source_dataset": "test",
            }) + "\n", encoding="utf-8")
            seeds = root / "seeds.jsonl"
            report = prepare_seeds(
                catalog, seeds, repository_limit=1,
                seeds_per_repository=1, master_seed=3)
            self.assertEqual(report["seeds"], 1)
            self.assertEqual(
                json.loads(seeds.read_text(encoding="utf-8"))["source"]["file"],
                "model.py",
            )
            recipe = {
                "schema_version": RECIPE_SCHEMA,
                "author_model": "kimi-author",
                "solver_model": "qwen-base",
                "audit_model": "kimi-audit",
                "attempts": 2,
                "master_seed": 13,
                "author_max_tokens": 1000,
                "solver_max_tokens": 1000,
                "audit_max_tokens": 1000,
                "timeout": 20,
                "send_seed": False,
            }
            queue = WorkQueue(root / "queue.sqlite")
            counts = enqueue_seeds(queue, seeds, root / "recipe.json", recipe)
            self.assertEqual(counts["inserted"], 1)
            self.assertEqual(
                enqueue_seeds(queue, seeds, root / "recipe.json", recipe)["existing"], 1)
            with patch.dict("os.environ", {
                "SCICODE_KIMI_BASE_URL": "http://kimi/v1",
                "SCICODE_KIMI_API_KEY": "dummy",
                "SCICODE_QWEN_BASE_URL": "http://qwen/v1",
                "SCICODE_QWEN_API_KEY": "dummy",
            }):
                run_workers(
                    queue, output_root=root / "output", recipe=recipe,
                    workers=2, kimi_slots=1, qwen_slots=1, chat_fn=fake_chat,
                )
            self.assertEqual(queue.counts().get("done"), 1)
            result = aggregate(queue, root / "native.jsonl", root / "train.jsonl")
            self.assertEqual(result["sft_rows"], 2)
            native = [json.loads(line) for line in
                      (root / "native.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len({row["trace_id"] for row in native}), 2)
            self.assertEqual(native[0]["messages"][0]["content"], QUESTION)
            self.assertEqual(len((root / "train.jsonl").read_text(
                encoding="utf-8").splitlines()), 2)

    def test_cpp_source_can_seed_a_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            (snapshot / "solver.cpp").write_text(
                "double energy(double mass, double velocity) {"
                " return 0.5 * mass * velocity * velocity; }\n",
                encoding="utf-8")
            (snapshot / ".scicodepile_snapshot.json").write_text(
                json.dumps({"snapshot_hash": "sha-cpp"}), encoding="utf-8")
            catalog = root / "catalog.jsonl"
            catalog.write_text(json.dumps({
                "source_kind": "scicodepile_clean_dataset",
                "repo_id": "scicodepile:example/cpp",
                "full_name": "example/cpp",
                "snapshot_path": str(snapshot),
                "snapshot_hash": "sha-cpp",
                "source_dataset": "test",
            }) + "\n", encoding="utf-8")
            seeds = root / "seeds.jsonl"
            result = prepare_seeds(
                catalog, seeds, repository_limit=1,
                seeds_per_repository=1, master_seed=3)
            self.assertEqual(result["seeds"], 1)
            self.assertEqual(
                json.loads(seeds.read_text(encoding="utf-8"))["source"]["file"],
                "solver.cpp",
            )


if __name__ == "__main__":
    unittest.main()
