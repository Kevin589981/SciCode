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
请求显式加入 `thinking: {type: enabled}` 和 `reasoning_effort: high`。真实调试发现
仅换模型名称时内部端点未返回 reasoning_content；不能假定代理继承官方默认。
参数依据 [DeepSeek 官方思考模式文档](https://api-docs.deepseek.com/guides/thinking_mode/)。
官方说明思考模式下 temperature 不生效，因此 0.7 只记录为请求值，不能宣称教师
采样温度与 Qwen 完全等效。当前 high 为显式基线，可在新运行目录另试 max。
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
新批次可用 `bash factory/reasoning/run_4586_deepseek_reuse.sh pipeline` 一次串联
生成→导出→科学审核→thinking 审核→分流。默认仍是 validate，不会误启动。
pipeline 在任何生成请求错误时停止下游；原始产物保留，可先 run 续跑。
若已完成导出而审核中断，用各阶段命令恢复，不自动覆盖旧 native/reviewed 目录。
这些恢复规则避免复用已过时的审核输入，也不会停止其他输出目录的已有请求。

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
thinking 审核额外要求可逐字定位的推理引文及效率分类；最终答案中的引文不能
冒充 thinking 引文，过短的单字引文也不接受。正式 SFT 仅接受 efficient 或
productive_but_long；repetitive/uncertain 仍保留完整记录，不静默删除答案。

## JSONL 与不丢失答案的边界

- `solver/traces.jsonl`：原始 thinking 与 content 分字段保存，含终止原因、usage、
  模型、请求参数、可见题面哈希。重试追加，不覆盖旧原始 trace。
- `solver/traces.errors.jsonl`：历史请求错误事件，不是尚缺样本的实时数量。
- `solver/raw-responses.jsonl`：校验前保存的实际请求消息与已聚合服务响应，写入
  有线程锁。即使服务漏掉 thinking 导致 trace 校验失败，返回的答案仍可追溯。
  不含密钥或请求头；完全无有效 trace 时 export 拒绝生成看似成功的空数据集。
- `solver/run_manifest.json`：无密钥的不可变输入与生成身份，改变题面/预算/模型/
  温度/代码版本等不得复用旧输出。并发可调整，不改变训练样本的生成身份。
- `solver/progress.json`：原子写入的当前轮 written/errors/skipped/total_jobs；
  是本轮计数，不把历史 error 计作永久失败。中断时最后快照可能滞后一个写入。
- `native-v1/sft.jsonl`：完整生成的结构候选，每行 `id/messages/metadata`；
  assistant 为 `<think>完整推理</think>最终答案`。metadata 明确 `not_reviewed`。
  reasoning_metrics 记录字符数及教师 API 报告的 reasoning/text tokens，不能将
  教师计数误当成学生有效训练 token。
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

## 2026-10-01 实测验收记录

- 全量输入：4586/4586，2182 个仓库，最多每仓库 8 题，准备阶段拒绝 0。
- 原始 4586 数据 SHA256：
  `2894ca0e6a9df3e1e5c22c35053082512374b19a0b029941070718264c274424`。
- 启用 thinking 后的真实两题 smoke：并行 2，max_tokens=196608；两题均 stop、
  未截断、无请求错误。续跑 skipped=2，无追加请求，成功导出两条完整 SFT 候选。
- 第一道 diagnose_revise：25696 reasoning tokens + 2550 final text tokens，
  用时 148.196 秒；第二道 compare_justify：53321 + 4552，用时 297.536 秒。
  这是服务端 API 的计数，不是 Qwen 学生 token。
- 两道都有 Python fence。第一道主实现可被 ast.parse 解析，另一个为带 `>>>`
  的 REPL 输入/输出示例（标签更适合 pycon）；第二道两个 fence 都可解析。
  没有执行这些生成代码，保留原始响应，不把示例片段当成主实现语法错误。
- 第一条还用同一 DeepSeek 做过一次诊断性 thinking 评分，JSON 能通过旧评分
  schema，但该自评不是独立科学审核，也不满足后来新增的引文证据门槛。
- 科学审核与双通道 release 路径有离线端到端测试；尚未对这两条正式执行 Kimi
  科学性审核。不能把这两条结构候选称为已科学审核合格的训练数据。
- 长请求的原 SSH 返回通道发生滞留；直接读取服务器产物确认两题早已完成。
  后续 SSH 调试使用 ServerAliveInterval/ServerAliveCountMax，正式批次批准后
  应在服务器后台运行并看文件进度，而不依赖长时间 SSH 输出返回。

真实样例目录：
`/root/ScienceIDE-workspace/SciCode-deepseek-distill-20261001/data-reasoning-deepseek-smoke-2-v2/`。
原始 traces 在 solver；初版 native-v1 保留；验收最新版结构导出另存 native-v2。
完整 4586 条批次只有 inputs，未创建 solver 目录、未启动合成。
