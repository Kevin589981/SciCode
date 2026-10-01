# DeepSeek 同题蒸馏：运行与验收

正式批量合成必须等待用户验收。当前只允许准备输入、离线测试和少量接口调试。
Qwen 基线分支保持不动；本分支独立运行，不修改历史 4586 条数据。

## 入口与产物

工作区：`/root/ScienceIDE-workspace/SciCode-deepseek-distill-20261001`。
环境复用 uv 创建的 `/root/scicode-factory-venv`；生成链不需要训练/RL 依赖。
配置：`factory/reasoning/configs/deepseek_v4flash_0731_reuse_4586.json`。

```text
4586 条 supported-CoT-answer 旧数据（只读）
  → prepare：原题、精确 system/user、来源与 SHA256
  → validate：身份/哈希/上下文预算，不发请求
  → run：DeepSeek 重新解题，不传旧 assistant
  → export：原生 thinking + 最终答案；另存审核输入
  → audit：科学答案审核（旧审核不继承）
  → grade：完整 thinking 的学习价值审核
  → select：五类完整记录 + 双重审核支持的 sft.jsonl
```

默认模型 `DeepSeek-V4-Flash-0731`，端点 `http://10.100.184.69:4000/v1`。
默认 500 并行，本程序配置硬上限 2000。此为本批次请求上限，不是服务端全局
余量探测；其他客户端占用并发时需要留余量。线程池 pending 队列有界，主线程
负责 JSONL 和进度写入，不走 SQLite。单输出目录有排他锁，不能被两进程并写。

输出上限默认 196608，上下文预算 262144，idle timeout 2400 秒。上下文校验用
UTF-8 字节数加 4096 模板余量作保守估计，**不是教师 tokenizer 精确认证**；服务
若有更小的实际限制，以接口调试结果为准，不能声称配置值证明部署能力。
HTTP 超时是每次阻塞读的 idle timeout，不是整个请求的总时长上限。

密钥仅由环境变量传入，不放进配置/脚本/manifest。审核模型默认单个 Kimi-K3，
同一模型分角色调用，不要求人工审核或多模型投票。审核单独用
`SCICODE_REVIEW_BASE_URL` / `SCICODE_REVIEW_API_KEY`，不隐式复用解题服务。

## 无模型请求的准备/验收

```bash
cd /root/ScienceIDE-workspace/SciCode-deepseek-distill-20261001
PY=/root/scicode-factory-venv/bin/python
ROOT="$PWD/data-reasoning-deepseek-supported4586-v1"

"$PY" -m factory.reasoning.distill prepare \
  --sft /root/ScienceIDE-workspace/SciCode-v3/datasets/scicode-v1-supported-cot-answer-sft-4586-v1/sft.jsonl \
  --index /root/ScienceIDE-workspace/SciCode-v3/data-reasoning-10k-v1-cleaned-v1/index.jsonl \
  --source-root /root/ScienceIDE-workspace/SciCode \
  --out-dir "$ROOT/inputs" --target 4586 --per-repo 0

bash factory/reasoning/run_4586_deepseek_reuse.sh validate
"$PY" -m pytest tests/factory -q
```

`--per-repo 0` 为不施加新的仓库上限，避免改变既定 4586 条比较群体。
prepare 要求输入每条都是 `model_supported_answer` + `reasoning_and_answer`；
验证题目与旧可见 prompt、task hash、索引根目录，并拒绝重复 ID 和非空输出目录。
若准备已完成，不重复执行 prepare；直接 validate。

## 用户批准后才运行的步骤

以下均不是验收时自动执行的命令。生成与审核密钥由用户/安全环境提前设置。

```bash
# 会调用 DeepSeek；只在批准后执行。
bash factory/reasoning/run_4586_deepseek_reuse.sh run

"$PY" -m factory.reasoning.distill export \
  --inputs "$ROOT/inputs" --output "$ROOT/solver" --out-dir "$ROOT/native-v1"

# 会调用独立审核服务；同一个审核模型即可。
"$PY" -m factory.reasoning.distill audit \
  --native "$ROOT/native-v1" --out "$ROOT/scientific-audit.jsonl" \
  --reviewer Kimi-K3 --workers 64 --max-tokens 65536

"$PY" -m factory.reasoning.distill grade \
  --inputs "$ROOT/inputs" --native "$ROOT/native-v1" \
  --out "$ROOT/reasoning-quality.jsonl" --reviewer Kimi-K3 \
  --workers 64 --max-tokens 65536

"$PY" -m factory.reasoning.distill select \
  --native "$ROOT/native-v1" --audit "$ROOT/scientific-audit.jsonl" \
  --grades "$ROOT/reasoning-quality.jsonl" --reviewer Kimi-K3 \
  --out-dir "$ROOT/reviewed-v1"
```

审核不执行学生代码，也不证明科学真值。科学审核分别生成只看题面的要求/反例
计划、逐项答案检验、必要时交叉矛盾检查；thinking 质量沿用 outcome-independent
评分规则，不按 assert pass/fail 直接判定。所有模型审核结论仍是证据而非证明。

## JSONL 与不丢失答案的边界

- `solver/traces.jsonl`：原始 thinking 与 content 分字段保存，含终止原因、usage、
  模型、请求参数、可见题面哈希。重试追加，不覆盖旧原始 trace。
- `solver/traces.errors.jsonl`：历史请求错误事件，不是尚缺样本的实时数量。
- `solver/run_manifest.json`：无密钥的不可变输入与生成身份，改变题面/预算/模型/
  温度/代码版本等不得复用旧输出。并发可调整，不改变训练样本的生成身份。
- `solver/progress.json`：原子写入的当前轮 written/errors/skipped/total_jobs；
  是本轮计数，不把历史 error 计作永久失败。中断时最后快照可能滞后一个写入。
- `native-v1/sft.jsonl`：完整生成的结构候选，每行 `id/messages/metadata`；
  assistant 为 `<think>完整推理</think>最终答案`。metadata 明确 `not_reviewed`。
- `native-v1/audit-candidates.jsonl`：同一回答的分字段审核输入，SHA256 绑定。
- `native-v1/excluded.jsonl`：截断/缺通道/边界歧义说明与原始 trace 位置。
  不删除原始内容；不因截断就宣称 thinking 无价值，但不混入完整答案比较集。
- `reviewed-v1/model_supported_answer.jsonl`：科学答案获支持的完整 CoT+答案。
- 其余 `reasoning_candidate` / `quarantine` / `audit_error` / `not_reviewed` 文件
  **仍保留同一完整 CoT+答案**，只是改变分类，绝不静默裁掉最终答案。
- `reviewed-v1/sft.jsonl`：答案获支持且 thinking 质量 trainable、两通道均获学习
  支持的严格子集。没有传入 thinking grades 时此文件为空，不能冒充完整验收。

DeepSeek 导出不套用 Qwen 专属重复-final修复；独立原生通道直接保留；只有单个
开头 `<think>...</think>` 包装可以确定性拆分，其他歧义保留原始记录并隔离。

## 续跑与观察

重复 `run` 跳过已正常完成的 trace，错误和截断 trace 可以重试。已有完成 trace
若重复、模型/题面不匹配，或 manifest 身份变化，拒绝请求并要求新输出目录。
export 接受同身份未完成→完成的追加历史，不接受已完成→重复改写。

```bash
watch -n 30 'cat /root/ScienceIDE-workspace/SciCode-deepseek-distill-20261001/data-reasoning-deepseek-supported4586-v1/solver/progress.json'
```

## 暂不包含的训练工作

本轮交付的是合成/审核实现，不启动 SFT、测评、数据库上传或 math/STEM 混合。
API usage 不是学生有效监督 token。后续对齐训练预算仍须固定 Qwen tokenizer、
chat template、loss mask、截断和 packing 配置；教师响应数相同不等于 token 等量。
