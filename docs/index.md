# 文档索引与权威顺序

- **状态**：立项基线
- **生效日期**：2026-07-21
- **责任人**：Gate 0 任命

本文定义 OneClick2D 文档职责及冲突处理顺序。新增文档必须有独立目的、责任人和维护节奏，避免重复来源。

## 权威顺序

出现冲突时按以下顺序处理，并在同一变更中修复冲突：

1. 法律、安全、隐私和项目硬边界；
2. 已由不可变 Gate 记录具名批准的 ADR；Proposed ADR 不具有该权威；
3. 项目章程与不可变 Gate 记录；
4. 产品需求中的稳定 ID（FR/NFR/TEL）；
5. 版本化机器可读契约与格式规范；
6. 架构、质量、评估和开发规范；
7. 路线图、风险与开放决策；
8. README、贡献指南、模板和示例。

标记为“候选”或“暂定”的数值只是待验证假设；下级文档不能将其写成承诺。

## 产品与交付

- [项目章程](PROJECT_CHARTER.md)：目标、用户、范围、非目标及治理。
- [产品需求](PRODUCT_REQUIREMENTS.md)：规范性行为与验收标识。
- [可行性预研](FEASIBILITY_SPIKE_PLAN.md)：Gate F 技术假设、协议和停止条件。
- [MVP Gate 计划](MVP_PLAN.md)：Gate 0/F/1/2/3/4 顺序和证据。
- [资源与关键路径](RESOURCE_AND_CRITICAL_PATH_PLAN.md)：人员、外部依赖和重新基线规则。
- [用户修正与降级](USER_RECOVERY_AND_FALLBACK_UX.md)：失败结果的最小可恢复路径。
- [数据采购与权利](DATA_ACQUISITION_AND_RIGHTS_PLAN.md)：Gate 数据、预算和资产台账。
- [容量与成本](CAPACITY_COST_CONTROL.md)：接受前预留、预算和成本分母。
- [Gate 0 记录](gate-records/GATE_0.md)：不可变证据、具名批准和决定。
- [Gate F 技术预注册](gate-records/GATE_F_TECHNICAL_PREREGISTRATION.md)：冻结 D-004 技术协议；D-003/D-009 关闭并由 Gate 0 绑定前不激活。

## 架构与格式

- [系统架构](ARCHITECTURE.md)
- [CIR / `.oc2d` 逻辑规范](CIR_SPEC.md)
- [`.oc2d` 包一致性规范](PACKAGE_CONFORMANCE.md)
- [PSD 导出配置](PSD_EXPORT_PROFILE.md)
- [模型动态研究初稿](MODEL_MOTION_DRAFT.md)
- [单项模型 Candidate 技术预检](../examples/gate-f-model-candidate/README.md)

## 质量、工程与治理

- [质量计划](QUALITY_PLAN.md)
- [评估规范](EVALUATION.md)
- [开发规范](DEVELOPMENT_STANDARDS.md)
- [度量与遥测](MEASUREMENT_TELEMETRY.md)
- [风险登记](RISK_REGISTER.md)
- [开放决策](OPEN_DECISIONS.md)
- [外部声明证据矩阵](RELEASE_CLAIMS_MATRIX.md)
- [模型卡模板](templates/MODEL_CARD.md)
- [数据集卡模板](templates/DATASET_CARD.md)
- [ADR-0001：一期产品与格式边界](adr/0001-phase-1-product-and-format-boundary.md)

## 隐私、安全与合规

- [隐私与安全](PRIVACY_SECURITY.md)
- [数据处理清单](privacy/DATA_PROCESSING_INVENTORY.md)
- [第三方许可登记](legal/THIRD_PARTY_LICENSE_AND_NOTICE_REGISTER.md)
- [发布法律准备度](legal/LAUNCH_READINESS.md)
- [下架、滥用与申诉](operations/TAKEDOWN_ABUSE_AND_APPEALS_RUNBOOK.md)

## 仓库规则

- [贡献指南](../CONTRIBUTING.md)
- [安全报告](../SECURITY.md)
- [Pull Request 模板](../.github/pull_request_template.md)
- [Claude Code 项目说明](../CLAUDE.md)

## 变更规则

- 产品范围、阈值、保留期、遥测、法律/品牌声明或兼容性变化必须有 ADR 或正式决策记录。
- 契约字段只能按兼容性规则演进；删除或改变已采纳格式的语义必须提升主版本并提供迁移。尚未采纳的 0.x disposable spike（含 v0.1）可以被不兼容替换，但必须被读取器明确识别为不支持，且不得进入 current/current-1 或耐久性声明。
- Gate 记录签署后不可覆写，只能新增后续记录。
- “待定”不等于默认允许；统一采用 [开放决策](OPEN_DECISIONS.md) 的限制性默认值。
- 看板、会议和演示不是权威证据，除非不可变报告或决策链接写入 Gate 记录。
