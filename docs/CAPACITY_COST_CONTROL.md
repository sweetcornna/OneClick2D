# 容量与成本控制

## 1. 标准生成配置

Gate 1 决定并版本化：输入上限、模型/precision、GPU/CPU profile、最大 stage time、retry/downshift、缓存、region/currency、storage/egress、shared idle 归因和测量窗口。

## 2. 指标

- **per eligible start**：该 start 的 GPU/CPU/storage/request/egress/retry/cancel 实际可变支出；按成功、失败、取消、超时报告 p50/p95；
- **blended cohort per verified success**：cohort 全部支出除 verified success 数；零成功视为失败/无限；
- 单独报告 failed-spend share 和 idle allocation。

## 3. 接受前预留

Generate 接受前检查账号/日/月额度、并发、队列年龄、worker capability、最坏情况预算和 artifact storage。无法预留时返回稳定原因码和 `retry_after`，不能接受后因预算静默丢弃。

## 4. 阈值阶梯

Gate 1 设置 50/75/90/100% 的 daily/monthly actions：观察、限制邀请/并发、暂停高成本 profile、拒绝新 starts。已预留任务必须明确完成、取消或失败，不得悬挂。override 要求具名权限、期限和审计。

## 5. 运行控制

CPU 安全/适用性预检先于 GPU；每账号初期一个 active generation；按 oldest eligible age 扩容并限制 fleet；OOM 只允许一次经验证 downshift；重试必须是明确 transient/resource code。

## 6. 对账与演练

每日/每周将 usage ledger 与 provider bill 对账，调查漏记/重复。Gate 3 前演练 budget circuit breaker、provider 价格变化、queue backlog、OOM storm 和成功率骤降；验证 trip/reset、用户提示和 kill switch。
