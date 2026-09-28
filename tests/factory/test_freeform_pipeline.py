import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.reasoning.freeform import (
    AUDIT_SCHEMA,
    SFT_SCHEMA,
    TASK_SCHEMA,
    audit_trace,
    collect_trace,
    compose_task,
    qwen_messages,
    sample_parameters,
    training_projection,
    training_row,
)
from factory.reasoning.freeform_batch import (
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
QUESTION = "在这个模型中，观测值加倍后平衡量将如何变化？说明适用条件。"


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
            "reference_answer": "平方关系成立时，平衡量变为四倍。",
        }, ensure_ascii=False))
    if model == "qwen-base":
        if messages != [{"role": "user", "content": QUESTION}]:
            raise AssertionError("Qwen received an altered question")
        return response(
            "若模型确为平方关系且其他条件不变，平衡量变为四倍。",
            reasoning="设平衡量为 x 的平方。输入变为 2x，则输出为 4x²。",
        )
    if model == "kimi-audit":
        return response(json.dumps({
            "verdict": "accept",
            "reason": "推理与平方关系一致，限定条件也写明。",
        }, ensure_ascii=False))
    raise AssertionError(model)


class FreeformPipelineTests(unittest.TestCase):
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
                         "设平衡量为 x 的平方。输入变为 2x，则输出为 4x²。")
        self.assertEqual(trace["prompt"], qwen_messages(task))
        audit = audit_trace(
            task, trace, chat_fn=fake_chat, model="kimi-audit",
            base_url="http://kimi/v1", api_key="dummy",
            max_tokens=1000, timeout=20,
        )
        self.assertEqual(audit["schema_version"], AUDIT_SCHEMA)
        row = training_row(task, trace, audit)
        self.assertEqual(row["schema_version"], SFT_SCHEMA)
        self.assertEqual(row["messages"][1]["reasoning_content"],
                         trace["reasoning_content"])
        projected = training_projection(row)
        self.assertEqual(projected["messages"][0]["content"], QUESTION)
        self.assertIn("<think>\n设平衡量", projected["messages"][1]["content"])
        self.assertTrue(projected["messages"][1]["content"].endswith(trace["content"]))

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
            recipe = {
                "schema_version": "scicode-freeform-recipe-v1",
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


if __name__ == "__main__":
    unittest.main()
