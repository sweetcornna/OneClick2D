# 开放决策登记

- **状态**：每周复核
- **原则**：假设不是批准；逾期采用限制性默认值。

| ID | 决策 | Owner/Gate | 所需证据 | 默认值 | 状态 |
|---|---|---|---|---|---|
| D-001 | 五类责任人和容量计划 | Program / G0 | 具名 owner/reviewer、person-weeks、外部 lead time | 只做本地 spike | Open |
| D-002 | 仓库/产品/贡献及代码/模型/数据/字体/素材许可 | Product/Legal / G0 | 业务分发、义务、专利、notices | 私有、不可分发 | Open |
| D-003 | Gate F 数据/模型预算及采购路径 | Product/ML/Legal / G0 | 数量、合同、权利、吞吐、候选模型 | 仅目的创建 feasibility | Open |
| D-004 | Gate F 假设、切片、对照、kill criteria、强制参数 | ML/Graphics/Product / G0 | [技术预注册决策](gate-records/GATE_F_TECHNICAL_PREREGISTRATION.md)；Gate 0 激活时绑定 immutable tree | 无发布参数承诺；D-003/D-009 仍阻止计分运行 | Closed (technical decision; not activated) |
| D-005 | 前后端语言/框架/包工具/renderer/schema toolchain | Technical / G1 | Gate F 后 spike、维护/安全 | framework-light disposable | Open |
| D-006 | 最简架构：DB claim runner vs managed queue/object | Technical / G1 | failure semantics、成本、运营触发 | in-process fake transport | Open |
| D-007 | 云/地区/OIDC/store/processor/no-backup/上传下载授权 | Tech/Privacy/Legal / G1 | provider proof、删除、replay/revoke | 无真实用户云；proxy download | Open |
| D-008 | CIR v0.2/package ABI/digest/limits/readers/signing | Format/Security / G1 | real conformance fixture/tamper | v0.1 可丢弃，无 durability claim | Open |
| D-009 | PSD writer/reader/精确 Photoshop/Krita/测试权利 | Export/Legal / F–G1 | exact roundtrip/editor/license | 停止双输出；需重新立项 | Open |
| D-010 | 浏览器 tracker/runtime/model/redistribution/设备 | Frontend/ML/Privacy / G2 | quality/perf/network/license | manual only | Open |
| D-011 | reference GPU/browser/load/benchmark protocol | Tech/QA / G1 | exact hardware/driver/profile | 无泛化性能声明 | Open |
| D-012 | essential processing vs optional analytics | Product/Privacy/Legal / G3 | purpose/fields/basis/consent/retention | analytics off | Open |
| D-013 | 单所有者账户/OIDC/admin/support access | Security/Product / G1 | MFA/JIT/exceptional access | internal accounts、no pixel viewer | Open |
| D-014 | 市场/terms/privacy/content/output/abuse/AI transparency | Legal/Product / G3 | launch readiness | 无外部 beta；EU/地区不开放 | Open |
| D-015 | 临时代号/域名/包/扩展/更名 | Product/Legal / G3 | clearance/ownership/rename | internal codename only | Open |
| D-016 | 格式支持：G1 决定精确 predecessor/current 读取、迁移语义/报告/fixtures；G4 决定支持期限、current-1 政策、deprecation、服务结束/reader | Product/Format / G1+G4 | 一致性样例、迁移验收、支持成本/用户影响 | v0.1 disposable 不进入支持；无 offline/indefinite claim | Open |
| D-017 | Gate 4 统计分析记录 | QA/Product / G3 | Wilson pass table、slice/missing/adjudication | exploratory only | Open |
| D-018 | 标准配置、成本 ceiling、budget ladder | Platform/Product / G1 | per-start/blended 方法、billing | 固定 invite/concurrency | Open |
| D-019 | artifact/model registry、SBOM、签名/authenticity | Security/Platform / G2 | identity/revoke/rollback | 内部摘要，无 authenticity claim | Open |
| D-020 | collaboration/mobile/batch/billing/permanent library | Product / Post-MVP | 重新立项 | Deferred | Deferred |
| D-021 | 官方第三方专有格式工作 | Product/Legal/Tech / Gate C | 官方能力、许可/费用/商标、ADR | Closed；无代码/fixture/逆向 | Closed |

## 程序

跨切面/难逆选择使用 ADR；阈值/统计使用具名产品/评估决策。记录 options、环境、证据、假设、权利/隐私/安全/成本、迁移/回滚和 revisit。批准后同步需求、架构、契约、测试和风险。
