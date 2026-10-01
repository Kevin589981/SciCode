# SciCode 科学推理合成数据流水线说明

> 供课题讨论的技术说明；根据 SciCode-v3 工作树提交 82b868ba2dc9cf596aa7db6eb20f3a2d1dc039a1 的代码整理，日期：2026-09-27。本文描述**代码已经实现的机制**，不是“已经证明生成数据正确”的结论。代码若继续修改，以相应提交的源码和运行产物为准。

## 1. 目标、范围与设计原则

这套流水线借鉴 ScienceIDE 的“自动发现对象—出题—让模型作答—审查—导出数据”的生产思想，落到 SciCode 的**科学计算任务和推理轨迹**上。目标不是复刻 ScienceIDE 的真实依赖环境、执行型智能体评测、RL 或 SFT 训练器，也不是只生成容易用断言判对错的 QA。目标是得到能训练科学思维的题目和可保留原生思考内容的 SFT JSONL。

生成的题目应要求模型组合多个认知操作，例如建立假设、推导关系、比较适用条件、诊断现象、修订方法和解释边界。解题是否通过辅助测试不直接决定 trace 是否值得学习；即使最终答案不完整，过程也可能有价值。但“值得学习的过程”和“科学正确的最终答案”是两个不同命题，不能因一个审查分数高就混为一谈。

本文区分三层：

1. **主生产流水线**：SciCodePile 清洗代码集 → 仓库筛选与源码函数选择 → 自动造题 → 难度/科学性前置审查 → 求解并保留 thinking → 轨迹价值评分 → SFT 导出。
2. **可选后置科学答案审核**：对已经导出的候选样本再作题目要求拆解、反例探测、逐项答案审查和跨要求矛盾检查，分流“答案候选 / 推理候选 / 隔离”。目前不是主流水线的内置准入条件；曾用于旧 v1 批次的清洗。
3. **辅助难度评估与人工校准代码**：源码中存在独立评估工具，但当前批量脚本没有把它作为主生产环节；不能误称已自动完成了这些保证。

## 2. 总体流程

~~~mermaid
flowchart TD
    A["SciCodePile 已清洗文件级数据集<br/>固定数据集 revision"] --> B["建立文件索引，按关键词分层轮转选择仓库"]
    B --> C["从清洗数据重建局部源码快照<br/>catalog + snapshot_hash"]
    X["可选第二通道：科学关键词 → GitHub 搜索<br/>当前 10k 脚本未启用"] -.-> D
    C --> D["持久化仓库任务队列<br/>仓库级 lease、目标样本数预留"]
    D --> E["科学相关性筛查 / 许可证信息"]
    E --> F["AST 挖掘 Python 函数<br/>词项双证据排序，每仓库选 3～16 个"]
    F --> G["Kimi 按三种题型造题<br/>题目 + 私有 reasoning_contract"]
    G --> H["结构门槛 + Kimi 深度 critic"]
    G --> I["Kimi 来源与科学性 verifier<br/>至少两段真实源码引文"]
    H --> J["通过双重准入的题目 → Kimi solver"]
    I --> J
    J --> K["保留原生 reasoning_content<br/>以及最终 content、截断状态"]
    K --> L["Kimi 推理价值 judge<br/>逐 assistant 消息 loss 标注"]
    L --> M["导出仓库 SFT shard<br/>按 trace_id 汇总到 accepted-sft.jsonl"]
    M -. "可选、单独运行" .-> N["问题盲审式要求/反例计划"]
    N --> O["逐项审查最终答案"]
    O --> P["必要时跨要求矛盾检查"]
    P --> Q["答案候选 / 推理候选 / 隔离"]
~~~

表中的 Kimi 是运行脚本当前配置的 Kimi-K3；系统架构允许各环节改用不同模型。当前默认使用**同一个模型分别承担作者、批评者、验证者、求解者、轨迹评审者**，所以“多个角色”不等于“独立模型共识”。

### 2.1 数据源与仓库选择

主通道读取 SciCodePile/SciCode-Domain-Code 的**已清洗文件级 CSV**（此前下载的数据集规模约 83.7 GB），固定 revision 为 2909e04dcf5957b52108aace56b9ae773cdac758。程序索引 CSV 中的仓库名、相对路径、内容等，只选择至少含 Python 文件的仓库，然后按数据集附带的科学关键词分层、稳定排序、轮转取样。它**直接把清洗后的文件还原成局部源码快照**；主通道不会根据仓库清单回 GitHub 下载原始仓库。每个快照有内容散列，catalog 记录数据集、revision、快照路径和 hash。重建后的仓库不一定具有原仓库的全部文件或可运行环境，这也是本项目不把执行通过率当作科学思维代理指标的原因之一。

当前另有可选关键词/GitHub 通道：本提交的 science_keywords.txt 实际为 **40 个种子词**（早期讨论中的“41 个”与当前文件不符），可由 Kimi 最多再扩展 40 个搜索词；GitHub 搜索结果可按语言、star、大小等条件过滤、固定提交并浅克隆。它用于后备或混合来源，**10k 启动脚本明确设置 repository_source=scicodepile，未启用 GitHub 通道**。

局部仓库先提取 README（上限约 32,000 字符）；若 README 不足 100 字符，则从最多 12 个 Python 文件取源码摘录（每文件最多约 4,000 字符，总量约 32,000 字符）。Kimi 给出“真正实现科学计算”的相关性判断，要求 relevant=true 且 confidence≥3。仓库级许可证未识别时通常拒绝；SciCodePile 主通道若有数据集许可证信息，代码允许进入，但这不等于已逐文件核实再分发权，数据合规仍须独立评估。

对通过仓库做 AST 函数挖掘：每模块最多 4 个、每仓库最多 1,200 个候选。候选排名用“仓库科学主题词 × 函数/文档字符串/源码中的词项证据”的余弦相似度乘积；先倾向不同模块，再补同模块候选。需要至少 3 个候选，最多选 16 个。**这是词法相关性筛选，不是语义科学正确性证明**。选出的候选轮换映射到 3 种题型，因此 16 个候选最多尝试造 16 道题；最终有用 SFT 行数没有固定产率，可能是 0，也可能因任务失败、审查淘汰或模型响应失效而远低于 16。

### 2.2 三种题型与“学生可见”边界

| 题型 | 要求模型做什么 | 必需的公开输入 |
|---|---|---|
| derive_implement | 由科学假设推导方法，分析至少两种条件/约束，再实现 | assumptions 至少 2 项；derivation_target 指明要推导的对象而不直接给结果 |
| diagnose_revise | 根据现象区分至少两种原因，诊断并修订方法 | observations 至少 2 项；candidate_causes 至少 2 项 |
| compare_justify | 比较至少两种方法的适用条件，按明确标准做选择并解释 | alternatives 至少 2 项；decision_criteria 至少 2 项 |

每题包含：

~~~text
schema_version, task_id, archetype,
source {repo, commit, file, symbol, license, module_hint, source},
problem {question, background, ...任意其他公开字段},
deliverable {kind, requirements, ...任意其他公开字段},
reasoning_contract {cognitive_operations, scientific_concepts,
                    evidence_expected, failure_modes, forbidden_shortcuts},
archetype_payload {题型必需字段, ...任意其他公开字段},
authoring {模型、参数、尝试次数、响应位置、usage 等}
~~~

source 和 reasoning_contract 保存在任务的私有元数据中，供出题与相应审查/追溯使用；solver 看到的是 problem、deliverable、**全部 archetype_payload 字段和各公开部分附加字段**。这个边界由统一的 student_view.py 渲染，策略版本 student-visible-v3；深度 critic、来源 verifier、solver、轨迹 judge、导出器都复用/校验同一个可见版本的 hash。v1 旧设计曾漏呈 archetype_payload，因而旧数据里可能存在“出题模型知道、解题模型不知道”的信息不一致；v3 修了这一点，但旧行不会因此自动变成 v3 合规行。

### 2.3 造题后的准入与求解

造题输出必须是 JSON：question 至少 120 字符、background 至少 60 字符、deliverable 至少两条、私有认知操作至少三种、科学概念至少两个，并符合各题型公开输入的数量约束。结构不合格可尝试重新调用作者模型；重试时只追加“前次结构验证失败，请在预算内输出完整 JSON”的提醒。

深度前置审查分两层。确定性结构规则要求私有认知操作中至少 3 类非“实现”操作，题目文字本身至少呈现 2 类推理操作；若任务要求代码/实验，题干还必须明确实现要求。语义 critic 从科学深度、步骤依赖、决策需求、非平凡性四项各打 0～4 分：平均≥3，科学深度≥2、步骤依赖≥2、非平凡性≥2，并且没有必要输入缺失或答案已泄露。另一路来源 verifier 检查题目是否可由**学生可见材料加标准科学知识**回答、内部是否一致、源码证据是否确实支持；要求至少两处不同的真实源码支持引文，科学有效性、来源性、可回答性、约束一致性均≥3，防捷径≥2，不得有致命问题、缺失输入或已暴露答案。实现顺序是先算 preflight 再算 verification，但 verifier 会检查该仓库生成的全部任务，不只检查 preflight 已通过的题目；solver 才取两者交集。上述均为“模型判断+局部确定性校验”，不是数学证明。

仅两道门都通过，solver 才收到统一渲染的题目和科学推理 system 提示。解题记录保留 assistant 的原生 reasoning_content 与 content、finish_reason、是否截断、用时、token usage、模型/任务/快照/工厂提交/hash 等。默认每题采 1 次，温度 0.7；允许中断时仍保留已流出的思考，但 trace schema 要求至少一条 assistant 消息中有非空原生 reasoning_content；完全没有原生思考的响应无法成为此流程的合格 trace。当前主流程没有为 SciCode 题目配置必需的可执行测试，outcome 默认为 not_run / auxiliary_check；因此**不是“assert 通过即收、失败即丢”**。

### 2.4 轨迹评分、SFT 行与后置审查

轨迹 judge 见到同一学生题面、完整记录的 trace、截断状态及弱诊断性质的 outcome，按 scientific_validity、causal_coherence、strategy、evidence_use、self_correction、insight_density、degeneracy 七项 0～4 分，并对每条 assistant 消息标注是否训练思考、是否训练最终内容。确定性训练门槛为：scientific_validity≥3、causal_coherence≥3、strategy≥2、evidence_use≥2、insight_density≥2、degeneracy≤2，且至少有一处允许训练；self_correction 无硬阈值。失败的辅助检查不自动淘汰，直接通过也不自动入选；非逻辑死循环的部分思考可以保留。

导出器只联接当前策略下被两道前置审查接纳的 task、当前 trace 与对应 judge grade；再次验证 student_view_hash 和真实 user 文本。每个 SFT JSONL 行保留 system/user/assistant 消息；system、user 的 loss=false；assistant 分开保存 content 和 reasoning_content，并有 reasoning_loss、content_loss 和总体 loss。默认 thinking_format=separate_reasoning_content，不把思考硬拼入 content（另有可选 inline_thinking）。tools=[] 反映当前无工具调用。特别注意：**JSONL 携带 loss 标记不代表任意训练脚本都会自动尊重它**；训练适配器必须显式读取 reasoning_content 及两种 loss mask，否则仍会丢掉推理监督。

单仓库产生 reasoning/sft.jsonl 等分片；批处理按 trace_id 去重、核验冲突，汇总 accepted-sft.jsonl 及 batch.tasks / preflight / verification / traces / grades.jsonl。目标 10,000 行是批处理上限/终止条件，不等于保证产出 10,000 条合格数据；当来源、筛查、响应质量或资源不足时会短缺。汇总报告记录候选数、合格数、按仓库产率等。

可选后置 scientific_audit.py 专门针对**最终答案科学正确性**，不读长 thinking 和旧 judge 分数：先只看问题拆成原子硬要求与独立边界/反例探针；再看冻结计划与最终答案逐项检查；若全部满足且答案可训练，再看各项判断间是否藏有矛盾。它把样本分为 model_supported_answer、reasoning_candidate、quarantine，选择器进一步生成“只训练答案”和“只训练推理候选”的两份 JSONL，各自屏蔽另一部分 loss。此处“model-supported”仍只表示**同一 Kimi 模型的审查支持**，不是独立科学真值。隔离不等于原始 trace 被删除；原始 JSONL 仍保留。旧 v1 批次的清洗/审核属于另一路历史产物，不能据此声称 v3 已通过后置审核。

## 3. 批量生产、并发与复现

当前 10k 启动器 factory/reasoning/run_10k_kimi.sh 的配置是：目标 10,000 个 SFT 行，500 个仓库 worker，模型请求槽动态 500～1,792；模型接口 Kimi-K3，context_window_tokens=262,144，作者输出上限 131,072，solver 196,608，critic 16,384，verifier 65,536，judge 32,768，judge 输入上限 900,000 **字符**，单调用 timeout 7,200 秒。每仓库选 3～16 题、目标预留 5 行，首批目录上限 3,600 仓库，扩展目录上限 19,559。配置的 262k 是**整个上下文窗口**，每阶段 max_tokens 是各自的**输出上限**；并不等于每次都能把 196k token 用于思考，输入和输出仍共享窗口。启动器无参数时只显示配置；需要 --execute 才运行初始批次，--prepare-and-enqueue 才扩大已清洗数据目录。

| 模型调用 | 10k 配置的 temperature | 单次输出 token 上限 | 是否属于主批次 |
|---|---:|---:|---|
| 仓库相关性 profile | 0.0 | 4,096（函数默认） | 是 |
| 造题 author | 0.3 | 131,072 | 是 |
| 深度 critic | 0.0 | 16,384 | 是 |
| 来源 verifier | 0.0 | 65,536 | 是 |
| 求解 solver | 0.7 | 196,608 | 是 |
| 轨迹 judge | 0.0 | 32,768 | 是 |
| 科学答案 audit | 0.0 | 单独命令决定，默认 16,384 | 否 |

批量任务存于 SQLite（job / job_progress / job_events / 资源槽），以仓库快照与 recipe 的 hash 去重，领取 lease、定期续租、失败限次重试。当前本地控制器把已完成/已预留样本数物化在 job_progress 中，worker 经主进程条件变量协调，不再每次领取遍历全部完成任务。主进程内模型/仓库槽减少高并发 SQLite 写入；跨进程 worker 仍使用持久化槽。运行脚本指定 SQLite DELETE 日志模式，用于避免在不适合 WAL 的共享/虚拟文件系统上的问题；锁冲突有有限重试。每仓库另有限制同一 repo_id 同时只跑一个任务。Prometheus 指标每 30 秒读取全局活跃请求数，扣除本进程活跃量估算其他使用者流量，再把本进程准入上限调整到 500～1,792。**500 是设定的下限，若外部服务已满，这一政策本身不能保证绝不超用共享服务**；指标失败也回落到下限而非停机。

每仓库保存 source_snapshot.json、repository_profile.json、mined.jsonl、selected.jsonl、repo_meta.json、repository_report.json 和 reasoning 子目录；子目录有 tasks、preflight、verification、traces、grades、sft、sft.report、run_manifest。manifest 记录模型/预算/输入文件摘要/产物摘要/工厂提交。recipe 固定工厂 commit；运行中改代码会被发现并拒绝继续，不应在正在跑的批次上热替换版本。

### 3.1 复现入口与产物检查（以下是说明，不在撰写本文时执行）

在 yicloud 的 SciCode-v3 工作树中，脚本默认使用 uv 建立的 /root/scicode-factory-venv/bin/python。若 SciCodePile 原始 CSV 和预处理 catalog 已在默认位置，则可先阅读配置、跑小规模 smoke，再考虑启动大批次：

~~~bash
cd /root/ScienceIDE-workspace/SciCode-v3
bash factory/reasoning/run_10k_kimi.sh
bash factory/reasoning/run_v3_smoke.sh
bash factory/reasoning/run_v3_smoke.sh --execute
# 明确决定投入 500 worker 的大规模生产后才运行下一行：
bash factory/reasoning/run_10k_kimi.sh --execute
~~~

如果只有 SciCodePile 原始 CSV 而缺少 catalog，可用独立准备命令；它的 --skip-download 表示绝不重新下载数据，--repository-limit 决定从清洗数据重建多少个局部仓库快照：

~~~bash
/root/scicode-factory-venv/bin/python -m factory.reasoning.scicodepile_dataset +  --raw-dir /root/ScienceIDE-workspace/SciCode/.cache/scicodepile/raw +  --prepared-root /root/ScienceIDE-workspace/SciCode/.cache/scicodepile/prepared +  --catalog /root/ScienceIDE-workspace/SciCode/.cache/scicodepile/catalog.jsonl +  --repository-limit 3600 --skip-download
~~~

大批次启动器还支持 --prepare-and-enqueue：在已有原始 CSV 上准备最多 19,559 个清洗源码快照，并加入**已存在的** 10k 队列。它不适合作为首个启动动作。检查队列与产物时可以只读执行：

~~~bash
cd /root/ScienceIDE-workspace/SciCode-v3
/root/scicode-factory-venv/bin/python -m factory.reasoning.batch status +  --db data-reasoning-10k-v3/batch.sqlite3
wc -l data-reasoning-10k-v3/accepted-sft.jsonl
~~~

accepted-sft.jsonl 应连同 batch.*.jsonl、accepted-sft.report.json 及各仓库 reasoning 分片阅读；只看行数不能判断科学质量。后置答案审核另行运行 scientific_audit.py review 与 select，并须用**新的输出路径**；select 在存在未审完、错误记录、输入 hash 变更时拒绝物化，不能将它视为自动随大批次完成的步骤。

## 4. 数据示意（省略长文本，不是实际样本）

~~~json
{
  "schema_version": "scicode-reasoning-sft-v1",
  "task_name": "repo-symbol-derive_implement-...",
  "trace_id": "repo-symbol-...::...",
  "task_hash": "sha256...",
  "archetype": "derive_implement",
  "student_view_policy": "student-visible-v3",
  "messages": [
    {"role": "system", "content": "You are a scientific reasoning specialist...", "loss": false},
    {"role": "user", "content": "完整的学生可见题目...", "loss": false},
    {
      "role": "assistant",
      "content": "最终回答...",
      "reasoning_content": "模型原生思考...",
      "reasoning_loss": true,
      "content_loss": false,
      "loss": true,
      "quality": "good",
      "quality_rationale": "具体理由..."
    }
  ],
  "tools": [],
  "thinking_format": "separate_reasoning_content",
  "outcome": {"status": "not_run", "kind": "auxiliary_check"},
  "termination": {"finish_reason": "stop", "truncated": false, "max_tokens": 196608, "usage": {}},
  "trace_quality": {"scores": {}, "rationale": "...", "judge": {}},
  "provenance": {"factory": "scicode-reasoning-factory", "trace": {}, "grade_id": "..."},
  "automatic_review": {
    "policy_version": "automatic-scientific-review-v1",
    "mode": "single_model",
    "human_review_required": false
  }
}
~~~

上例刻意把 content_loss=false 以说明思考与答案可以分开给 loss；实际值逐条由 judge 的 message_annotations 决定。automatic_review.mode=single_model 只描述审查配置，不构成正确性认证。科研使用前应保留原始题目、trace、评分和源码证据以便回溯。

## 5. 已有保证、尚未保证与建议汇报口径

**已实现的工程约束**：清洗数据集固定 revision 与快照 hash；题目公开/私有字段边界统一渲染；最少认知操作和公开输入的结构检查；两道模型前置准入；来源引文与源码子串比对；完整原生 thinking 留存；按消息、按思考/内容分开的 loss；任务/trace/审核 hash 与恢复机制；批量仓库去重、限流、lease 和失败记录。

**不应宣称的保证**：来源数据预清洗不等于科研结论正确；源码存在不等于任务的全部假设成立；单模型扮演多角色不等于独立专家复核；词项相似度不等于科学概念覆盖；题面要求“多步”与 judge 高分不能严格证明认知深度；保留思考不等于思考全过程无误；后置答案审核无法证明每一步 thinking 正确；无执行环境/形式验证意味着数值边界、单位、公式与代码一致性仍可能漏检；10k 目标不等于足量产出或质量保证。旧 v1 产物的部分问题与隔离率不能直接外推到 v3，v3 也不能因修复了题面字段就被视作已完成大规模验证。

建议向老师将其表述为：**“一套具备来源可追溯、题面一致性校验、推理轨迹保留和分层自动审查的科学计算合成数据工厂；目前自动审查结果是候选 SFT 数据，不是科学正确性的证书。下一步需通过固定的困难题、已知反例和抽样复核量化误收率、真实推理深度与训练收益。”**

## 6. 完整模型提示词（当前代码原文模板）

以下英文块为本提交中的提示词**模板原文**；花括号中的表达式是运行时插入的 JSON、源码、题面或 trace，不是给模型看的字面量。除注明“可选”外，对应主流水线的各模型阶段。temperature / 输出上限以第 3 节的 10k 脚本覆盖值为准。

### 6.1 仓库科学相关性筛查（repository.py）

~~~text
Judge whether a public repository actually implements computational-science methods or workflows.

Do not accept a repository merely because it mentions scientific terms, collects
papers, contains only documentation, or wraps a generic AI/application stack.
Require evidence in the supplied README or cleaned source excerpts of implemented
numerical, simulation, modeling, scientific-analysis, or domain-computation
functionality.

METADATA:
{json.dumps(metadata, ensure_ascii=False, indent=2)}

REPOSITORY CONTEXT (README when retained, otherwise source excerpts):
{readme}

Return ONLY one JSON object:
{
  "relevant": true,
  "confidence": 0,
  "reason": "evidence-based explanation",
  "domain_keywords": ["specific scientific or numerical concepts"],
  "summary": {
    "project_overview": "what scientific work the repository performs",
    "methods": "major methods, models, or algorithms",
    "dependencies": "major scientific dependencies",
    "inputs_outputs": "typical scientific inputs and outputs",
    "runtime": "runtime/build expectations"
  }
}
Confidence is an integer 0..4. Use relevant=true only when the README supports it.
~~~

其中 metadata 来自仓库名、描述、topics、语言、stars、匹配关键词；readme 变量可能实际是清洗源码摘录。

### 6.2 自动造题主提示词（prompts.py）

~~~text
You are authoring a reasoning-intensive scientific computing task for SciCode.

The training target is the solver's scientific reasoning, not merely whether a
short function passes tests. The task must require a chain of distinct cognitive
operations such as deriving, comparing assumptions, diagnosing evidence,
selecting a method, analyzing regimes, or revising a model. Reject in your own
reasoning any task that reduces to translating a docstring, copying the source,
or implementing branches mechanically.
The solver will see problem.question, problem.background, deliverable.kind and
requirements, and EVERY archetype_payload field, but will NOT see the source
or reasoning_contract. All necessary observations, assumptions, alternatives,
and criteria must be in those visible fields. Payload fields must state neutral
givens, never the diagnosis, preferred alternative, derived result, or answer.
You may add task-specific public fields (for example available_primitives or
constraints) to problem, deliverable, or archetype_payload; they will all be
shown verbatim to the solver. Never put a reference solution in these fields.
Payload list entries may be strings or structured objects when the latter make
scientific inputs clearer; either form is fully visible to the solver.
In particular, derivation_target names what must be derived; do not write the
resulting equation, argmin, algorithm steps, or correctness condition there.
Do not hide essential numerical values in reasoning_contract.evidence_expected.

Task archetype: {archetype}
{ARCHETYPE_GUIDANCE[archetype]}

GROUNDING SOURCE (private author evidence; never mention it in the problem):
{json.dumps(source_context, ensure_ascii=False, indent=2)}

Return ONLY one JSON object with these keys:
{
  "problem": {
    "question": "self-contained task, at least 120 characters",
    "background": "scientific context, assumptions and meanings, at least 60 characters"
  },
  "deliverable": {
    "kind": "analysis" | "analysis_and_code" | "analysis_and_experiment",
    "requirements": ["at least two explicit deliverables"]
  },
  "reasoning_contract": {
    "cognitive_operations": ["at least three distinct operations"],
    "scientific_concepts": ["at least two concepts"],
    "evidence_expected": ["at least two signs of a strong reasoning trace"],
    "failure_modes": ["at least one scientifically meaningful reasoning failure"],
    "forbidden_shortcuts": ["at least one shortcut that makes the task shallow"]
  },
  "archetype_payload": {"use the archetype-specific keys described above": "..."}
}

Do not emit tests, reference code, a solution, markdown fences, or prose outside
the JSON object.
~~~

插入的三种题型指导文本如下（每次只插入当前题型的一段）：

~~~text
[derive_implement]
Build a derive-and-implement task. The solver must connect explicit scientific
assumptions to a mathematical or numerical method, reason about at least two
regimes or constraints, and then implement the result. Do not turn the source
docstring into a longer paraphrase.

archetype_payload must contain:
- assumptions: at least two explicit assumptions;
- derivation_target: the relation or algorithm the solver must derive.

[diagnose_revise]
Build a diagnose-and-revise task. Give scientifically meaningful observations
of a plausible but flawed method or result. The solver must distinguish at
least two candidate causes, use the observations to diagnose the issue, and
construct a revision. The symptom must not reveal the diagnosis directly.

archetype_payload must contain:
- observations: at least two diagnostic observations;
- candidate_causes: at least two plausible causes the solver must distinguish.

[compare_justify]
Build a compare-and-justify task. Present at least two plausible scientific or
numerical approaches whose suitability changes with the stated regime. The
solver must compare them using explicit criteria, justify a choice, and produce
the requested implementation or analysis.

archetype_payload must contain:
- alternatives: at least two methods or models;
- decision_criteria: at least two scientific or numerical criteria.
~~~

若作者模型首次返回的结构不合格，第二次调用在主提示词末尾原样追加：

~~~text
A prior response failed structural validation. Keep internal reasoning concise and emit the complete final JSON object before the token budget is exhausted.
~~~

### 6.3 学生题面渲染模板（student_view.py；这是代码规则而非另一次模型调用）

~~~text
{problem["question"]}

Scientific background:
{problem["background"]}

Task type: {derive and implement | diagnose and revise | compare and justify}

[每个 problem 附加字段，按字段名字典序]
Additional problem information ({key}):
{value}

[当前题型必需 payload 字段，按下表的固定次序]
derive_implement:
  Assumptions supplied with the problem:
  {assumptions}
  Derivation target:
  {derivation_target}
diagnose_revise:
  Observations to diagnose:
  {observations}
  Candidate causes to distinguish:
  {candidate_causes}
compare_justify:
  Alternatives to compare:
  {alternatives}
  Decision criteria:
  {decision_criteria}

[每个 payload 附加字段，按字段名字典序]
Additional task information ({key}):
{value}

Deliverable type: {deliverable["kind"] 中下划线换为空格}
Deliverables:
- {deliverable["requirements"][0]}
- {deliverable["requirements"][1]}
- ...

[每个 deliverable 附加字段，按字段名字典序]
Additional deliverable information ({key}):
{value}

Make the scientific method, assumptions, comparisons, and justification explicit before presenting the final implementation or analysis.
~~~

各区块之间以空行分隔；列表逐项加“- ”；非字符串/列表的公开值以 JSON 呈现。具体题目中只显示一个题型的 payload 区块，括号行表示渲染逻辑而非发给模型的字面内容。

### 6.4 科学推理深度 critic（preflight.py）

~~~text
You are an independent critic of a scientific reasoning task.

Judge whether solving the task requires dependent scientific reasoning rather
than docstring translation, mechanical branching, source recall, or verbose but
empty explanation. Do not solve the task. Judge only the exact student-visible
prompt below. Private source or rubric cannot repair missing inputs.
Inspect every section, including derivation targets and optional public fields.
If the prompt itself supplies the requested equation, algorithm, diagnosis, or
preferred alternative, set answer_exposed=true even if the prose is long.

STUDENT-VISIBLE PROMPT:
{render_student_user(task)}

Return ONLY this JSON object, using integer scores from 0 (absent) to 4 (strong):
{
  "scores": {
    "scientific_depth": 0,
    "multi_step_dependency": 0,
    "decision_requirement": 0,
    "nontriviality": 0
  },
  "missing_inputs": [],
  "answer_exposed": false,
  "shallow_failure_mode": null,
  "rationale": "specific evidence from the task"
}
~~~

### 6.5 科学与来源 verifier（verify.py）

~~~text
You are an independent scientific task verifier, not a solver.

Check whether the authored problem is scientifically defensible, answerable from
its stated background plus standard scientific knowledge, internally
consistent, grounded in the private source, and resistant to a shallow shortcut.
The source is verification evidence and is not shown to the eventual solver.
If a derivation target or any other public field states the requested rule,
diagnosis, or method choice, set answer_exposed=true regardless of source quality.

For every evidence item, copy an exact nontrivial quote from `source.source`.
Do not invent line numbers or paraphrase the quote. A deterministic checker will
reject quotes that do not occur in the source.

STUDENT-VISIBLE PROMPT (check answerability and shortcuts from this alone):
{render_student_user(task)}

PRIVATE SOURCE (grounding evidence only; it cannot repair missing student inputs):
{json.dumps(task['source'], ensure_ascii=False, indent=2)}

Return ONLY one JSON object:
{
  "scores": {
    "scientific_validity": 0,
    "source_grounding": 0,
    "answerability": 0,
    "constraint_consistency": 0,
    "shortcut_resistance": 0
  },
  "fatal_issues": ["empty unless the task is unusable"],
  "missing_inputs": [],
  "answer_exposed": false,
  "evidence": [
    {
      "claim": "task claim or requirement being checked",
      "source_quote": "exact quote copied from source.source",
      "assessment": "supports" | "contradicts" | "unclear"
    }
  ],
  "rationale": "specific scientific justification"
}

Scores are integers 0..4. Provide at least two distinct supporting evidence
quotes. A task with a false premise, missing information, contradictory
requirements, or an answer exposed by a mechanical shortcut must name that in
fatal_issues.
~~~

### 6.6 求解者 system 提示词（rollout.py）

~~~text
You are a scientific reasoning specialist. Work through the scientific
assumptions, competing methods, regimes, and failure modes carefully. Produce a
self-contained final response satisfying every requested deliverable. Do not
claim evidence you did not derive from the problem.
~~~

求解者 user 消息就是 6.3 的渲染结果；私有 source 和 reasoning_contract 不进入这条 user 消息。

### 6.7 推理训练价值 judge（grade.py）

~~~text
You are judging the TRAINING VALUE of a scientific reasoning trace.

Judge the reasoning itself. A failed auxiliary check can accompany an excellent
scientific trace; a passing check can accompany a trivial or lucky trace. Treat
the recorded check only as weak diagnostic evidence. Do not use it as the
selection rule. Long text is not inherently valuable: penalize repetition,
unsupported claims, dead loops, and confidently wrong scientific premises.

STUDENT-VISIBLE TASK (the only task information available to the solver):
{render_student_user(task)}

RECORDED TRACE (which may end before the final answer):
{json.dumps(trace_view, ensure_ascii=False, indent=2)}

If the stream was interrupted, assess the reasoning that was actually recorded.
Do not infer a missing final answer or automatically reject useful partial reasoning.

Assistant message indices that must each be annotated: {assistant_indices}

Return ONLY one JSON object:
{
  "scores": {
    "scientific_validity": 0,
    "causal_coherence": 0,
    "strategy": 0,
    "evidence_use": 0,
    "self_correction": 0,
    "insight_density": 0,
    "degeneracy": 0
  },
  "rationale": "specific evidence from the reasoning",
  "message_annotations": [
    {
      "message_index": {assistant_indices[0]},
      "train_reasoning": true,
      "train_content": true,
      "quality": "good" | "medium" | "bad",
      "rationale": "why these parts should or should not receive loss"
    }
  ]
}

All scores are integers 0..4. Higher is better except degeneracy, where 0 means
none and 4 means severe. `self_correction=0` is acceptable for a correct one-turn
trace. Include exactly one annotation for every listed assistant message index.
~~~

### 6.8 可选后置科学答案审核：问题盲审计划（scientific_audit.py）

~~~text
You are designing an INDEPENDENT scientific falsification plan. You have
only the student's question, not their answer. Extract each ATOMIC, hard
requirement, including exact invariants, boundary conventions, units, ranges,
algorithmic conditions, and deliverable constraints. Do not weaken an exact
requirement by adding your own assumptions. For each requirement propose a
concrete probe that would reveal a plausible wrong solution. Favor small or
degenerate values, limiting cases, and cross-checks against the definition.
If the question lacks essential givens or contradicts itself, state why.

QUESTION:
{prompt}

Return ONLY JSON:
{"task_status":"answerable|flawed|uncertain", "task_issue":"", "requirements":[
{"id":"R1", "requirement":"atomic requirement from question", "kind":"exact|conditional|qualitative", "probe":"specific independent test or check"}]}
Never invent a requirement not present in the question. Include all hard
requirements; keep the list concise by splitting compound requirements.
~~~

### 6.9 可选后置科学答案审核：逐项答案检查

~~~text
You are checking scientific correctness, not fluency or apparent effort.
The requirement/probe plan below was made WITHOUT seeing the answer. Test the
answer against EVERY requirement. An exact universal statement fails if one
legitimate counterexample exists, even if the answer acknowledges the caveat.
Trace length and a prior judge's score are irrelevant. Distinguish a flaw in
the question from a flaw in the answer. Do not pretend to execute code, consult
sources, or verify a fact you cannot actually check; mark it unverifiable.

QUESTION:
{prompt}

INDEPENDENT PLAN:
{json.dumps(plan, ensure_ascii=False)}

STUDENT ANSWER:
{answer}

For each R-id, cite a short exact phrase or formula from the answer (or say
"absent"), apply the proposed probe, and state the expected and answer-implied
result. For code, compare the IMPLEMENTED expression with the stated formula;
do not credit a correct derivation if the delivered implementation violates it.
Check arithmetic, units, boundary cases, and contradictions. A probe with no
conclusive result is unverifiable, not satisfied. Output ONLY JSON:
{"checks":[{"id":"R1", "status":"satisfied|violated|unverifiable",
"answer_evidence":"short exact quote/formula or absent", "probe_result":"concrete input and expected vs answer-implied result; or why unverifiable",
"explanation":"brief reasoning"}],
"critical_issue":"specific decisive contradiction, or empty string",
"summary":"brief overall assessment"}
~~~

### 6.10 可选后置科学答案审核：跨要求矛盾检查

~~~text
You are a SEPARATE final contradiction checker. You do not see the
student answer or the earlier review's confidence score. Only use the atomic
requirements and the review's concrete findings below. Look for a case where
one finding admits a delivered result that contradicts ANY other exact or
universal requirement, even when that finding calls the mismatch a caveat,
limitation, floor, approximation, or disclosed side effect. Disclosure does
NOT satisfy an exact requirement. Compare evidence across requirement IDs,
not just within one check. Do not add an unstated domain restriction. If the
evidence is insufficient, say uncertain rather than assuming consistency.

REQUIREMENTS AND INDEPENDENT PROBES:
{json.dumps(plan, ensure_ascii=False)}

ANSWER CHECKS:
{json.dumps(verdict, ensure_ascii=False)}

Return ONLY JSON:
{"status":"consistent|conflict|uncertain", "conflicts":[
{"required_id":"R1", "evidence_id":"R2", "input":"concrete case",
"required":"exact required result", "delivered":"answer-implied result",
"explanation":"why this contradicts the hard requirement"}],
"rationale":"brief justification"}
If any concrete contradiction exists, status MUST be conflict. If a result
cannot be established, status is uncertain. For consistent, conflicts is [].
~~~

### 6.11 可选辅助难度评估（difficulty.py；不参与当前 10k 主脚本的 SFT 准入）

~~~text
You are evaluating whether a solver actually solved a scientific
reasoning task. This is a DIFFICULTY measurement, not an SFT-value judgment.

Use the private source only as evidence. Accept scientifically correct methods
that differ from the source. Do not count length, confidence, or the auxiliary
outcome as correctness. Name every critical scientific error explicitly.

TASK AND PRIVATE SOURCE:
{json.dumps(task_view, ensure_ascii=False, indent=2)}

SOLVER RESPONSE:
{json.dumps(response, ensure_ascii=False, indent=2)}

Return ONLY one JSON object:
{
  "scores": {
    "scientific_correctness": 0,
    "constraint_satisfaction": 0,
    "reasoning_completeness": 0,
    "final_answer_adequacy": 0
  },
  "critical_errors": ["empty only when no critical error remains"],
  "rationale": "specific evidence for solved/partial/failed status"
}

All scores are integers 0..4. Do not output `solved`; it is derived by policy.
~~~

### 6.12 可选 GitHub 通道关键词扩展（discovery.py；当前 10k 脚本未使用）

~~~text
Expand a computational-science repository search vocabulary.

SEED TERMS:
{json.dumps(keywords, ensure_ascii=False)}

Return ONLY one JSON object {"queries": [...]} with at most {max_new} new
queries. Add synonyms, abbreviations/full forms, tool-ecosystem terms, and close
scientific subtopics. Keep every query specific to computational science; do
not add generic software or generic AI terms. Do not repeat seed terms.
~~~

### 6.13 旧 v1 候选清洗的对抗审核提示词（clean_legacy_sft.py；非 v3 主生产调用）

旧批次清洗曾使用下面的单次综合审查。它会传入有限的 reasoning_excerpt（超长时仅供审查地省略中段）以及私有作者字段，用于检查遗漏输入；原始 trace 不因此被裁剪。后续 scientific_audit.py 把最终答案的反例检查拆成 6.8～6.10 的独立调用。列出此提示词是为了完整记录既有自动审查路径，不表示 v3 批处理自动执行它。

~~~text
You are an ADVERSARIAL scientific SFT auditor. Recheck this sample independently.
The earlier Kimi score is NOT evidence. Find concrete counterexamples, missing
premises, false scientific claims, violated deliverables, and code/derivation
errors. Judge the STUDENT PROMPT alone for answerability: private author fields
were not shown to the student. A truncated but useful reasoning trajectory may
still train reasoning; an incorrect or unfinished final answer must not train
content. Do not reject merely because a trace is long or unfinished.

First extract EVERY hard requirement from the student prompt and ask whether the
answer meets it over its stated domain. In particular, an exact universal
requirement is violated by ONE legitimate counterexample; a caveat that admits
the violation does NOT make the requirement satisfied. If two hard requirements
are mutually inconsistent, task_status is flawed, even if the answer discusses
that inconsistency. If a solution chooses a convention that weakens an explicit
requirement, answer_status is exclude. Do not silently add new assumptions.

Then construct at least ONE NEW adversarial quantitative, logical, or edge-case
test that is not copied from the proposed answer's own examples. Checking only
the answer's examples is insufficient. Check units, limits, degeneracy, and
whether code implements the stated mathematics. Do not pretend to run code.
Quote question/answer text or provide concrete input and expected versus actual
outputs for any major flaw. Be skeptical but do not invent objections. If a
decisive claim cannot be checked, say uncertain rather than fabricating proof.
The reasoning excerpt may omit its middle FOR REVIEW EFFICIENCY; this does not
mean the original trace was interrupted. Use finish_reason for termination.

SAMPLE:
{json.dumps(view, ensure_ascii=False)}

Return ONLY one complete JSON object with:
{"task_status":"sound|flawed|uncertain",
  "reasoning_status":"train|exclude|uncertain",
  "answer_status":"train|exclude|uncertain",
  "requirement_checks":[{"requirement":"quoted hard requirement", "status":"met|violated|uncertain", "evidence":"specific check"}],
  "novel_counterexample":"new test input and expected versus actual result, or why no counterexample survives",
  "checks":["at least one specific independently checked claim"],
  "issues":[{"severity":"major|minor", "evidence":"exact short quote or explicit counterexample", "explanation":"why it matters"}],
  "summary":"brief rationale"}
If the answer is empty, answer_status must be exclude. If the student prompt
contradicts itself or omits indispensable given data, task_status must be flawed.
~~~

## 7. 代码与产物定位

| 内容 | 当前实现位置 |
|---|---|
| 大批次启动配置 | factory/reasoning/run_10k_kimi.sh |
| SciCodePile 清洗数据准备 | factory/reasoning/scicodepile_dataset.py |
| 仓库发现/分层轮转 | factory/reasoning/discovery.py |
| 队列/并发/汇总 | factory/reasoning/queue.py、batch.py |
| 仓库筛查、源码选择 | factory/reasoning/repository.py |
| 造题、题目 schema、统一题面 | factory/reasoning/author.py、prompts.py、schema.py、student_view.py |
| 前置审查、求解、评分与导出 | factory/reasoning/preflight.py、verify.py、rollout.py、grade.py、export.py、pipeline.py |
| 可选后置科学答案审核 | factory/reasoning/scientific_audit.py |
| 可选解题难度评估 | factory/reasoning/difficulty.py |

本文对“旧 v1 历史批次”只解释其与现行 v3 的边界；旧批次清洗脚本及其另行运行的审核可能正在被其他工作者修改，不能用本文代替实时运行报告。
