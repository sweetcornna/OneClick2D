# 度量与遥测计划

- **状态**：实施遥测前的规范
- **原则**：只收集支持具名决策的最小事实；禁止艺术和摄像头衍生数据。

## 1. 核心指标

| 指标 | 分母/定义 |
|---|---|
| 首次可用率 | 第一次完整生成满足 NFR-001 / 所有开始生成的合格锁定项；失败仍在分母 |
| 已验证可靠性 | 达到双验证成功 / 所有代表性合格 starts；只排除明确用户提前取消 |
| 用户任务 | 15 分钟内完成预览及双下载 / 全部进入研究的用户 |
| 不合格 recall | 被 block 的不合格样本 / 全部不合格样本 |
| false-block | 被 block 的合格样本 / 全部合格样本 |
| 删除 SLA | 用户请求后 15m，或自动撤销 deadline 后 15m 内 inventory clear / 全部删除请求及自动到期 |
| 每 start 成本 | 合格 start 产生的全部可变支出，按 outcome 报告 |
| blended success 成本 | cohort 全部支出 / verified successes；零成功为失败/无限 |

## 2. 默认状态

分析默认关闭。D-012 决定目的、法律基础、字段、处理者、保留和用户控制后才可启用。必要运行事件和可选产品分析使用不同 purpose/consent class。账号、权利确认、摄像头权限或邀请不等于分析许可。

## 3. 允许字段

schema version、UTC timestamp、random event/dedupe ID、可选 pseudonymous session、运营所需 opaque project/run/stage/attempt、app/model/pipeline/schema version、stable reason code、粗粒度环境/资源/时间/成本桶、boolean outcome 和 traffic class。

## 4. 禁止字段

艺术/缩略图/像素/蒙版/网格、文件名/层名、精确艺术 hash、路径/URL/签名授权、prompt/free text/任意 exception、摄像头帧/裁剪/关键点/嵌入/信号/校准/camera ID、姓名/email/token/cookie、精确硬件。生产时 allowlist；禁止先收后删。

## 5. 事件

服务器权威事件：upload accepted、validation completed、generation accepted/terminal、stage committed、export verified、delete requested/revoked/deleted。客户端只能报告与摄像头/跟踪路径完全独立的 UI 动作，不能断言服务器成功。

摄像头会话事实必须仅留在本地，包括权限/设备状态、tracker/model 状态、低置信结果、会话时长、frame time、latency、drop、校准和任何由摄像头或跟踪器派生的摘要。关闭 D-012 也不能授权传输或持久化这些字段。摄像头性能评估只使用本地记录的批准合成/目的创建测试，不进入产品遥测。

## 6. 数据质量

事件契约版本化并在生产入口验证；稳定 dedupe；每日将生成/导出/删除与权威状态 reconcile；报告 missing/late/duplicate/invalid；指标 SQL/规格使用分母 fixtures；定义变化保留旧版本，不能重写历史失败。

## 7. 保留和访问

在 D-012 前仅保留必要、内容无关的运营/安全日志，候选 ≤30 天。分析关闭即无产品分析数据。任何 incident extension 必须有目的、owner、access 和 expiry。权限按角色、查询审计，支持账号/分析删除。

## 8. 声明限制

低样本 Gate、探索集、短 soak 和内部 traffic 必须标注。100 个任务的硬不变量 run 不等于 98% 可靠性证明。不得从 page view、注册数、任务量或生成图数量推断质量。
