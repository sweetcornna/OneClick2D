# 质量与发布证据计划

- **状态**：规范性基线；候选阈值需 Gate 证据校准
- **规则**：任务完成不等于产品成功；硬不变量不能被平均分掩盖。

## 1. 可用性定义

### F-USABLE（仅 Gate F）

用于可丢弃 spike，不要求生产 `.oc2d` 包：无 severity-1；身份/中性保真达到同一人工锚点；自动拆层/有限补全及冻结的 Gate-F 评分能力可在 disposable IR 中渲染；几何/原作/身份硬检查通过；PSD 由独立 reader/editor 验证。

### P-USABLE（Gate 1 及以后产品结果）

必须同时满足：

- 无 severity-1；
- 身份/可识别度和中性保真各 ≥4/5；
- 图层/可编辑性和安全范围变形各 ≥3/5；
- Gate F 后批准的全部强制能力可演示；
- `.oc2d` 与 PSD 独立有效并绑定同一 revision；
- 无隐私、安全、删除和权利失败。

Tracking naturalness 仅在产品宣称摄像头能力时评分，不能使手动路径失效。

Severity-1 包括眼/口缺失或重复、身份改变的脸部补全、正常范围明显孔洞、控制反向、非法/无界几何、损坏包标记已验证、跨租户、摄像头外泄、删除系统性超时和客户内容二次使用。

## 2. 数据组合

- Gate F：20 个预注册、真实分层、权利明确素材；
- Gate 2：≥60 个 group-disjoint 研发/验证素材；
- Gate 3：独立验证集；
- Gate 4：100 个锁定合格素材，创作者/角色/近重复家族不跨 split；
- 不合格集：≥100，主要原因族各预注册且主要样本建议 ≥15；
- 合约/安全：程序生成项目、参数状态和 parser/archive 攻击语料；
- 性能/可靠性：可再分发、覆盖复杂度和资源边界的固定集。

每个资产记录权利依据、允许用途、不可变摘要、重复簇、责任人和下架路径。“合成”还需记录生成器/版本/条款、提示/参考来源和角色/商标/肖像审核。

## 3. Gate F kill gate

20 项全部运行自动路径。Gate 0 先冻结评分能力集合、static/simple-cutout comparator、paired primary metric、最小 superiority margin、不确定性和 tie/missing 规则。通过同时要求：至少 12/20 `F-USABLE`；自动路径按预注册 paired rule 优于 comparator；强制语义槽位存在率 ≥90%；任一 n≥3 预注册切片不为 0 成功；容差外原始可见像素零改写；全部几何有限/索引/拓扑有效；身份改变脸部补全零通过；PSD 证明通过。未达到 superiority 只能 RECHARTER/STOP。报告二项及 paired 不确定性，不作总体质量声明。

## 4. Gate 2/3 停止规则

- Gate 2 ≥60 项：观察首次可用率 ≥60%，报告双侧 95% 区间；全部硬不变量通过；
- Gate 3 独立验证：在揭盲前按精度/功效目标冻结最小样本量、区间式通过规则、切片和 missing 规则；全部锁定项入分母，missing/timeout/crash/invalid/export failure 为失败；观察首次可用率 ≥70% 且达到预注册区间门；
- 修正成功单独报告，不改写首次成功；
- 失败切片要以可验证 eligibility block 从声明范围移除并重新评估，或暂停。

## 5. Gate 4 统计规则

### 总体

锁定合格集使用双侧 95% Wilson 下界 ≥80%。若恰好 n=100，至少 88/100 可用。crash、timeout、缺失、无效和导出失败均为失败。

### 切片

冻结前定义关键切片/交叉、最小计数、重复家族、缺失、裁决和多重比较规则。每个声明的关键切片 n≥20、观察首次可用率 ≥65%，且不落后总体 >15 个百分点。更小切片只做探索性报告。

### eligibility

不合格 recall ≥95%，合格 false-block ≤10%；报告 Wilson 区间和逐原因表。除非做前瞻 power analysis，否则只称 benchmark observation。

## 6. 人工评审

评分锚点：

| 维度 | 1 | 3 | 5 |
|---|---|---|---|
| 身份 | 身份改变/关键脸部错误 | 可识别但局部偏差 | 明确同一角色 |
| 中性保真 | 大洞/双影/线色漂移 | 局部接缝 | 原作可见区域稳定 |
| 图层/编辑性 | 缺失/损坏 | 有限清理可用 | 语义连贯、生成区明确 |
| 变形 | 不安全/反向/露洞 | 有限范围可用 | 平滑、正确、稳定 |
| 跟踪（若宣称） | 不可用 | 可接受 | 校准后自然稳定 |

Gate 4 每项由至少两名训练评审盲评，严重分歧裁决，报告一致性。详细组件评估见 EVALUATION.md。

## 7. 用户证据

- Gate 1：≥5 位目标用户，验证边界/队列/到期/双输出理解；
- Gate 2：8–10 位，验证检查、修正、警告和拒绝摄像头；
- Gate 4：≥30 位，使用提供的权利明确评估素材；至少 24/30 在 15 分钟内无协助完成手动或校准预览及双下载，中位 active time ≤8 分钟；至少 27/30 理解 `.oc2d + PSD`、需检查和无 `.moc3`。

所有数字描述为观察研究结果，不外推总体确定性。

## 8. 性能、可靠性和成本

### 性能

每个认证设备/浏览器 profile ≥30 个热身后会话：验证 p95 ≤30s；生成 p50 ≤4m/p95 ≤10m；打包 p95 ≤60s；预览 p95 frame time ≤33.3ms；应用采集时间戳至 render submission p95 ≤150ms。每会话报告，不能汇总全部 frame 掩盖坏会话。

### 可靠性/可用性

≥500 个代表性合格开始，观察已验证成功 ≥98%；≥500 个声明五分钟预览，无崩溃 ≥99%；命名用户旅程连续 ≥30d 且 ≥10,000 probe-minutes，可用性 ≥99.0%。样本不足则保持为目标，不作证明。

### 成本

分别报告每个合格 start 的实际可变支出（按 outcome）和每个 verified success 的 cohort blended spend。失败、取消、重试、idle、storage、egress 全部按批准方法归因。数值上限由 Gate 1 决定。

## 9. 测试层级

- unit/property：策略、图、转换、色彩/alpha、插值、拓扑、路径/限制；
- schema/contract：正负例、当前/前一版本、unsupported major；
- integration：假传输/真实服务替身、attempt 所有权、恢复、删除；
- visual golden：合成/可再分发素材，容差和局部灾难检查；
- frozen model benchmark：切片、校准、资源和 prior 对比；
- browser/editor E2E：支持矩阵、摄像头降级、双输出；
- fault/load：中断、晚到、OOM、取消、budget stop；
- fuzz/security：媒体、ZIP/CIR/PSD、数值和资源；
- accessibility；
- privacy：网络/存储/日志 canary、删除/恢复、双租户。

硬边界测试不得为发布 quarantine。自动 rerun 不把失败变成功。

## 10. 发布硬不变量

零跨租户；零禁止摄像头出站/持久化；零客户内容二次使用；零原作容差外改写；零非法几何；零损坏 verified 下载；零删除超时；零未知/禁止权利；零可利用 critical/high；零专有格式边界违规。

100 个连续代表任务零 stuck/cross-tenant/corrupt verified export/deletion miss/severity-1，只证明硬不变量演练，不证明 98% 可靠性。

## 11. 发布报告

包含 app/pipeline/model/schema/benchmark 身份和摘要、数据组成/排除/权利、统计规则、切片和 CI、人工一致性、双输出矩阵、性能/资源/成本、可靠性/可用性暴露、删除/隐私/安全/可访问性、缺陷/例外/风险和具名 go/no-go。

当前 `validate_docs.py` 只能称“立项文档 lint”，不能称 contracts valid 或可行性通过。
