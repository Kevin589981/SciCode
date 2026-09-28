# SciCode 自由出题 / Qwen 原生 trace 流水线

本分支的新流水线位于 factory/reasoning/freeform.py 与 freeform_batch.py。原有固定 archetype 流水线仍独立可用。新流水线的学生可见内容只有 Kimi 原样写出的 question；出题素材、参考答案、审核结论只保存在内部工件中。

流程：从已清洗的 SciCodePile 本地快照抽样素材 → Kimi 自由写题与内部参考答案 → 基座 Qwen 对题面随机采样多条原生 thinking/answer → Kimi 对每条结果做一次综合审核 → 输出原生 trace、SFT 候选和训练视图。

## yicloud 上的小规模命令

在 /root/ScienceIDE-workspace/SciCode-diverse-self-distill 目录执行，以下目录仅作示例。先确认使用的 Qwen 服务确实是基座模型，Kimi 与 Qwen 各用独立的端点环境变量。密钥通过环境注入，脚本与 recipe 不保存密钥。

    PY=/root/scicode-factory-venv/bin/python
    export SCICODE_KIMI_BASE_URL=http://10.100.184.127:5050/v1
    export SCICODE_KIMI_API_KEY=EMPTY
    export SCICODE_QWEN_BASE_URL=QWEN_BASE_URL_HERE
    export SCICODE_QWEN_API_KEY=QWEN_API_KEY_HERE

    "$PY" -m factory.reasoning.freeform_batch prepare \
      --catalog /root/ScienceIDE-workspace/SciCode/.cache/scicodepile/catalog-10k.jsonl \
      --out .cache/freeform-v4-smoke/seeds.jsonl \
      --repository-limit 3 --seeds-per-repository 1 --master-seed 2026

    "$PY" -m factory.reasoning.freeform_batch author-smoke \
      --seeds .cache/freeform-v4-smoke/seeds.jsonl --index 0 \
      --out .cache/freeform-v4-smoke/author-task.json --master-seed 2026

    "$PY" -m factory.reasoning.freeform_batch trace-smoke \
      --task .cache/freeform-v4-smoke/author-task.json \
      --out .cache/freeform-v4-smoke/trace-check \
      --solver-model BASE_QWEN_MODEL_NAME --attempts 2 --master-seed 2026

    "$PY" -m factory.reasoning.freeform_batch enqueue \
      --db .cache/freeform-v4-smoke/queue.sqlite \
      --seeds .cache/freeform-v4-smoke/seeds.jsonl \
      --recipe .cache/freeform-v4-smoke/recipe.json \
      --author-model Kimi-K3 --solver-model BASE_QWEN_MODEL_NAME \
      --audit-model Kimi-K3 --attempts 2 \
      --author-max-tokens 16384 --solver-max-tokens 131072

    "$PY" -m factory.reasoning.freeform_batch run \
      --db .cache/freeform-v4-smoke/queue.sqlite \
      --recipe .cache/freeform-v4-smoke/recipe.json \
      --output-root .cache/freeform-v4-smoke/output \
      --workers 3 --kimi-slots 2 --qwen-slots 2

    "$PY" -m factory.reasoning.freeform_batch status \
      --db .cache/freeform-v4-smoke/queue.sqlite

    "$PY" -m factory.reasoning.freeform_batch aggregate \
      --db .cache/freeform-v4-smoke/queue.sqlite \
      --out .cache/freeform-v4-smoke/native-sft.jsonl \
      --train-out .cache/freeform-v4-smoke/train.jsonl

如服务支持请求级 seed，可在 enqueue 时加入 --send-seed。即使未发送 seed，非零 temperature、随机 top_p 和独立请求也会用于采样；实际输出是否出现有效变化应在 smoke 里检查。recipe 固定每次采样参数，任务重试可复现。要改变 recipe，使用新数据库与输出目录。

批量运行可以给 run 增加 --kimi-metrics-url http://10.100.184.127:29000/metrics，并分别调高 --workers、--kimi-slots、--qwen-slots。Kimi 服务容量默认按 1792 计算，可用 --kimi-service-capacity 调整；当前活跃请求数与本地槽共同决定新请求数。Qwen 端先用独立的静态并发槽，按其实际服务容量设置。

## 工件与边界

- seeds.jsonl：已清洗素材的抽样，含数据集快照哈希、文件、源码摘录。
- shards/<job_id>/task.json：Kimi 写出的 question 和内部参考答案。
- shards/<job_id>/trace-N.json：Qwen 实际收到的唯一 user 消息、原生 reasoning_content、final content、采样参数、响应原文及终止信息。
- shards/<job_id>/audit-N-<policy_version>.json：单次 Kimi 综合审核及简要依据。
- shards/<job_id>/sft.jsonl：仅含格式完整、科学审核通过且 Qwen 原生 thinking 与最终答案均存在的候选。
- native-sft.jsonl：跨 shard 的审计友好原生格式；train.jsonl：将同一条 Qwen 原生 thinking 确定性映射为 think 标签内容，供只读取 message.content 的训练模板使用。

所有原始响应保留在 shard 中。审核只影响 SFT 候选，不改写 Kimi 题面或 Qwen 的思维链。批量运行可用更多 worker，但 Kimi/Qwen 并发槽分别设置，并首先用少量真实样本检查题干、随机采样差异和训练模板读取结果。

审核文件实际名称包含 policy_version，例如 audit-0-scientific-overall-v2.json。审核提示词更新时可在同一目录留下旧判定并另存新版判定，方便比较校准；题目与 Qwen trace 保持原样。
