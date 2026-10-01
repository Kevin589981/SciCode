# DeepSeek/Kimi 4586 同题蒸馏：网关失败后的独立恢复

## 不能丢失的目标和约束

- 原题面的精确 system/user 不变；DeepSeek-V4-Flash-0731 重新生成完整 thinking + final。
- Kimi-K3 单模型分角色审核，生成和全部审核分别共享 500 并发槽，总预算都是 262144。
- 两模型使用同一公司网关、不同密钥；密钥只能在内存/环境中，不进入 Git、数据和日志。
- 不根据 assert pass/fail 直接判断思维链学习价值；审核不等于科学真值证明。
- 保留最终答案，不导出仅 CoT 来冒充完整 SFT。不启动训练、不改远程数据库。
- 新工作树、新输出恢复；旧文件、旧导出、旧 manifest 不修改。不检查无关模型工作区。

## 旧批次证据

旧代码 `8e3de38`，旧 PID 694268 已退出，目录：
`/root/ScienceIDE-workspace/SciCode-deepseek-distill-20261001/data-reasoning-deepseek-supported4586-v1`。

原始 2082 条 trace：1741 条标记 stop/未截断，341 条部分生成；其中一条 stop 只有
thinking、没有 final，真正有效双通道完成数是 1740。缺少有效完整生成 2846 条。
生成历史错误事件 8194 个（8193 HTTP 502、1 HTTP 503），不是 8194 个不同题目。
后两轮各 2845 次失败，均约 25 秒耗尽；旧机制短重试导致服务异常时快速消耗任务。
科学审核完成 292、error 1448：1403 为 HTTP 502、1 为 503、40 为遗漏 critical_issue、
3 为无有效 stream、1 截断。thinking 审核完成 83、error 1657：1621 为 HTTP 502，
36 为无法逐字验证的引文。旧最终双审核 SFT 18 条。

检查时模型列表和真实 chat 请求已经恢复 200；历史日志没保存 502 body，不能凭
这些数据断言后端故障由 GPU 容量、密钥或 500 并发本身引起。新版本保留 HTTP 状态、
脱敏 body 和 Retry-After，用于定位下一次异常。

## 修复

1. 每模型共享 circuit breaker：8 个连续瞬时失败后暂停新请求，30/60/120/240/300 秒
   退避；半开只准一个探测请求，成功后恢复。每模型新请求起始速度默认 50/s，不
   改变 500 并发槽；恢复预算 6 小时。401/403 等非瞬时错误立即闩锁，不不断尝试。
2. 移除嵌套 3x 短重试，网络失败在 transport 层恢复，不立刻将数千题记作失败。
3. 成功审核调用按实际完整 prompt/model/参数做 SHA256 原子缓存；后续失败不用
   重做前序计划。被语义校验拒绝的 response 保留，并标记不能直接复用。
4. 审核格式/引文失败最多三次，带失败反馈重新调用 Kimi，仍要求逐字引文，不做
   模糊匹配或把最终答案引文当成 CoT。失败响应也另存，审核 thinking 不用于 SFT。
5. `critical_issue` 漏填但 checks 已明确 violated 时，从既有 probe_result/explanation
   确定性补齐汇总，保留 violated；不把判错修改成判对。
6. distill 的完成条件必须包含非空 thinking + final；stop-only/缺答案任务可重试，
   原始失败 trace 不删，native exporter 只选择该身份后续完整记录。
7. `recover_distill` 验证原始输入、生成身份和所有审核 row hash 后复制到新目录。
   新 manifest 明确记录跨代码恢复 lineage，不伪造/覆盖旧 fingerprint。成功生成和
   成功审核跳过，error 重试。
8. 尚有缺失/审核错误时标记 needs_retry，不用 completed 混淆。最多三次流水线恢复
   轮，每次只补缺失/error；导出递增 native-vN/reviewed-vN，不覆盖既有快照；最终
   未完成则非零退出。`pipeline_report.json/latest_sft` 指向最新合格集。

## 独立恢复入口

工作树：`/root/ScienceIDE-workspace/SciCode-deepseek-recovery-20261001`。
分支：`fix/2026-10-01-deepseek-kimi-gateway-recovery`。
新数据：`data-reasoning-deepseek-supported4586-recovery-v2`。

```bash
cd /root/ScienceIDE-workspace/SciCode-deepseek-recovery-20261001
PY=/root/scicode-factory-venv/bin/python
OLD=/root/ScienceIDE-workspace/SciCode-deepseek-distill-20261001/data-reasoning-deepseek-supported4586-v1
NEW="$PWD/data-reasoning-deepseek-supported4586-recovery-v2"

# 一次性准备；若 NEW 已存在，不覆盖。
"$PY" -m factory.reasoning.recover_distill --source "$OLD" --root "$NEW" \
  --config factory/reasoning/configs/deepseek_v4flash_0731_reuse_4586.json

# 密钥和 reviewer endpoint 由运行环境注入。该命令会发请求。
SCICODE_DATA_ROOT="$NEW" SCICODE_REVIEW_WORKERS=500 \
  bash factory/reasoning/run_4586_deepseek_reuse.sh pipeline
```

`pipeline_progress.json` 的 teacher_provider/review_provider 给出 circuit 状态、累计
瞬时错误/恢复次数和探测等待时间；transport-events.jsonl 记录脱敏故障 body。
旧 8194 条历史生成错误会随只读源复制保留，不能用 errors 文件行数判断本次还缺
多少题。以当前轮 solver/progress、pipeline 当前错误计数和最终报告为准。
