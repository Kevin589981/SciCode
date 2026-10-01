# SciCode 自动造题：当前进度与复现

代码版本：`Kevin589981/SciCode` 的 `scienceide-pipeline-v3`，提交 `9fd494f7f393ea2f665b61622dcecf37a84ee9cc`。下文的代码说明和命令都以这个提交为准。进度核对时间：2026-09-29。

## 做了什么

我参考 ScienceIDE 的自动化生产流程，在 SciCode 的 `factory/reasoning` 中实现了“选科学源码—出题—审题—解题—评估 trace—导出 SFT”。目标是生产需要科学推理的编程题和解题轨迹；没有搬运 ScienceIDE 的环境构建、RL 或训练代码。

主数据源是固定版本的 `SciCodePile/SciCode-Domain-Code` 清洗文件数据集。准备程序直接读取文件内容，按关键词覆盖选取含 Python 的仓库，生成本地源码快照与 catalog；批处理使用这些快照，**不按仓库名重新从 GitHub 下载源码**。GitHub 关键词搜索的通道仍在代码中，但 1k、10k 启动脚本都指定了 SciCodePile 通道。

目前的出题模板有三类：`derive_implement`（推导并实现）、`diagnose_revise`（诊断并修正）、`compare_justify`（比较并论证）。每个仓库先做相关性判断和 AST 源码挖掘，再让 Kimi 基于选中的代码写题。题目依次经过推理深度检查、源码与科学内容核查，然后交给解题模型。原生 `reasoning_content`、最终 `content`、截断状态和实际提示词都会保留。最后由 Kimi 评价 trace 的训练价值，导出候选 SFT；可另跑答案科学性审核。

批量任务由 `batch.py` 管理：SQLite 保存任务、租约和重试；每个仓库写独立目录，完成后聚合 JSONL。v3 把完成/预留样本数存进队列，单进程控制器在内存中协调进度和模型并发，减少高并发下的 SQLite 写锁竞争。1k 脚本配置 200 个 worker；10k 脚本配置 500 个 worker，并根据模型服务 metrics 将请求并发调在 500–1792。两个脚本默认只打印配置，加 `--execute` 才运行。

代码入口：

| 文件 | 职责 |
| --- | --- |
| `factory/reasoning/scicodepile_dataset.py` | 数据集下载、索引、快照和 catalog |
| `repository.py`、`author.py` | 仓库筛选、源码挖掘、出题 |
| `student_view.py` | 生成解题模型实际看到的题面 |
| `preflight.py`、`verify.py` | 深度检查、源码和科学内容核查 |
| `rollout.py`、`grade.py`、`export.py` | 采集 trace、评估、导出 SFT |
| `batch.py`、`run_1k_kimi.sh`、`run_10k_kimi.sh` | 批处理与启动 |
| `scientific_audit.py` | 后处理阶段的答案审核 |

## 跑到了哪里

已完成的旧批次数据和 v3 新流水线的产出分开记。下面的文件在服务器 `/root/ScienceIDE-workspace/SciCode-v3`，不随 Git 提交：

| 文件 | 数量和用途 |
| --- | --- |
| `data-reasoning-10k-v1-cleaned-v1/candidate-cleaned.jsonl` | 9,440 条清洗后的候选 trace，含原始 thinking 和答案 |
| `data-reasoning-10k-v1-cleaned-v1/scientific-audit-v2.jsonl` | 对这 9,440 条的追加式科学审核记录 |
| `datasets/scicode-v1-audit-sft-4939-v1/sft.jsonl` | 4,939 条训练数据：4,734 条 thinking＋答案、204 条仅 thinking、1 条仅答案 |
| `datasets/scicode-v1-supported-answer-sft-4587-v1/sft.jsonl` | 4,587 条答案获模型审核支持的数据：4,586 条 thinking＋答案、1 条仅答案；SHA-256 为 `5fc42ae07ccf8cd49b017ce828ec5dddf91a523d6597bc61d3d3d19e224b87da` |

9,440 条候选的最终审核状态：4,587 条 `model_supported_answer`，4,501 条 `quarantine`，203 条 `reasoning_candidate`，149 条 `audit_error`。4,939 训练集包含后三类中部分未获答案支持的样本；4,587 文件只取答案获支持的部分。

4,939 条已经用于 Qwen3.5-35B-A3B Instruct SFT。最后一个 HF 检查点：

```text
/mnt/share/scicode-audit-sft-4939-qwen35-instruct-20260927-v1/train/hf_checkpoints/iter_0000318
```

部署后在 SciCode 上分别用 `medium`、`xhigh` 推理预算测评。按**完整 test 65 题、validation 15 题**统计，指标是正确子步骤数/全部子步骤数：

| 范围 | medium | xhigh |
| --- | ---: | ---: |
| test：65 题、288 个子步骤 | 57/288，19.79% | 68/288，23.61% |
| validation：15 题、50 个子步骤 | 15/50，30.00% | 21/50，42.00% |

四组运行都在 `/root/scicode-avacore/runs/`，目录名以 `scicode-qwen35-audit4939-iter318-temp06-` 开头，分别以 `medium-bg-test-c20-20260928-v1`、`xhigh-bg-test-c20-20260928-v1`、`medium-bg-validation-c20-20260928-v1`、`xhigh-bg-validation-c20-20260928-v1` 结尾。各目录的 `rollouts.jsonl` 是逐题测评轨迹，`launcher.log` 有汇总。4,587 子集目前没有对应的训练测评结果。

v3 自身的产量：现有 `data-reasoning-v3-smoke` 报告中，2 个仓库完成，选出 6 个源码候选，**任务、trace、SFT 均为 0 条**。核对时没有 `data-reasoning-10k-v3` 输出目录。下一步是先查明 v3 smoke 为什么未形成任务，再决定是否启动大批量生产；旧批次的 9,440 条不能算作 v3 的产量。

## 干净 clone 后怎么跑

以下命令在 Linux/bash 中执行。`factory` 从仓库根目录导入；Python 环境用 `uv` 建立。当前提交没有 `uv.lock`，这里固定的是代码提交和 SciCodePile 数据版本，不是第三方包版本。

```bash
git clone --branch scienceide-pipeline-v3 --single-branch \
  https://github.com/Kevin589981/SciCode.git SciCode-v3
cd SciCode-v3
git checkout --detach 9fd494f7f393ea2f665b61622dcecf37a84ee9cc

uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python pyarrow huggingface_hub pytest
.venv/bin/python -m pytest tests/factory -q
```

造题还需要 SciCodePile 清洗数据、还原快照和 catalog。这些都不在 Git 中。在原 yicloud 上，可复用 `/root/ScienceIDE-workspace/SciCode/.cache/scicodepile`；catalog 中的快照路径是绝对路径，移动 catalog 时也必须保留对应快照。如果是全新机器，下载并准备数据：

```bash
export SCICODE_DATA_ROOT=/absolute/path/to/scicode-data
.venv/bin/python -m factory.reasoning.scicodepile_dataset \
  --raw-dir "$SCICODE_DATA_ROOT/.cache/scicodepile/raw" \
  --prepared-root "$SCICODE_DATA_ROOT/.cache/scicodepile/prepared" \
  --catalog "$SCICODE_DATA_ROOT/.cache/scicodepile/catalog.jsonl" \
  --repository-limit 3600
```

下载的是固定版本的约 83.7 GB 清洗文件；准备程序还会创建索引和快照。原始 CSV 已在同一 `raw/data` 目录时，可加 `--skip-download`。完成后检查 `catalog.jsonl`、`catalog.jsonl.meta.json` 和快照目录。

先用独立输出目录跑小规模 smoke，不直接开 1k/10k：

```bash
# 若复用 yicloud 已准备的数据：
export SCICODE_DATA_ROOT=/root/ScienceIDE-workspace/SciCode
export SCICODE_REPO_ROOT="$PWD"
export SCICODE_FACTORY_PYTHON="$PWD/.venv/bin/python"
export SCICODEPILE_CATALOG="$SCICODE_DATA_ROOT/.cache/scicodepile/catalog.jsonl"
export SCICODE_OUTPUT_ROOT="$PWD/data-reasoning-v3-smoke-new"
export SCICODE_LLM_BASE_URL=http://10.100.184.127:5050/v1
export SCICODE_LLM_API_KEY=dummy
export SCICODE_LLM_MODEL=Kimi-K3

bash factory/reasoning/run_v3_smoke.sh            # 只显示配置
bash factory/reasoning/run_v3_smoke.sh --execute  # 发起实际请求

.venv/bin/python -m factory.reasoning.batch status \
  --db "$SCICODE_OUTPUT_ROOT/batch.sqlite3"
```

上述地址只适用于能访问该内网服务的机器。smoke 默认取最多 3 个仓库，目标 2 条 SFT；是否产生样本要看输出目录里的 `accepted-sft.report.json`、每仓库 `repository_report.json` 和 `batch.{tasks,preflight,verification,traces,grades}.jsonl`。确认题目与 trace 的质量、队列稳定性后，再查看 `run_1k_kimi.sh` / `run_10k_kimi.sh` 的配置并决定是否加 `--execute`。

## 数据格式和目前的质量判断

v3 工厂的 `accepted-sft.jsonl` 每行有 `task_name`、`trace_id`、`archetype`、`messages`、`termination`、`trace_quality`、`automatic_review`、`provenance`。assistant 消息分别保存 `reasoning_content` 与最终 `content`，并有 `reasoning_loss`、`content_loss` 标记。关联的 `batch.tasks.jsonl`、`batch.traces.jsonl`、`batch.grades.jsonl` 保留题目、原始轨迹与审核依据。

用于上述训练的 4,939 文件是另一种训练器格式：每行 `id`、`messages`、`metadata`，assistant `content` 中用 `<think>…</think>` 接 thinking 和答案。复用新产物训练时，需确认训练器实际读取 thinking、最终答案和 loss mask；不能仅凭 JSONL 里存在 `reasoning_content` 就认为它被监督到了。

当前质量结论很直接：已有规模化数据和一次完整 SFT 测评，但该检查点在 SciCode test 上只有 19.79%（medium）/23.61%（xhigh）子步骤正确率；提高推理预算有收益，尚未解决效果问题。答案审核覆盖的是最终答案，不等于逐步验证 thinking。v3 的新造题流程还没有形成可评估的 SFT 样本，先解决出题转化率，再看题目难度、trace 是否高效，以及训练后是否真正提升模型能力。
