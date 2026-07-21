# 数据处理清单

- **状态**：模板 / 外测前必须填完
- **责任人**：隐私责任人

每个字段/对象一行，不允许用“用户数据”“元数据”等笼统类别代替。

| ID | 数据字段/对象 | 来源 | 目的 | 分类 | 控制者/处理者角色 | 存储/地区 | 可访问角色 | 接收者/分处理者 | 法律基础占位 | 保留/删除 | 备份/恢复 | DSR/导出 | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| DPI-001 | opaque account ID | OIDC | 账户范围 | pseudonymous | TBD | TBD | control plane | OIDC/provider TBD | 律师决定 | TBD | 内容无关备份 | TBD | Open |
| DPI-002 | rights attestation + policy version + timestamp | 用户 | 处理授权记录 | restricted audit | TBD | control DB | legal/ops 限定 | TBD | 律师决定 | TBD | 可按政策 | TBD | Open |
| DPI-003 | quarantine/canonical artwork | 用户 | 安全解码/生成 | confidential content | TBD | ephemeral object/region TBD | job scope | cloud TBD | 律师决定 | FR-019 | 禁止 | 删除 | Open |
| DPI-004 | layers/masks/fills/mesh/project/PSD | 系统 | 生成/导出 | confidential derived | TBD | ephemeral object | job/verifier | cloud TBD | 律师决定 | FR-019：成功终态+24h撤销，最迟+24h15m清空；其他最早期限+15m | 禁止 | 删除/下载 | Open |
| DPI-005 | object keys/dimensions/digests/content catalog | 系统 | 引用/完整性 | sensitive metadata | TBD | no-backup catalog | control plane | cloud TBD | 律师决定 | 随内容 | 禁止或 crypto-erasure | 删除 | Open |
| DPI-006 | webcam frames/crops | 本地设备 | 本地预览 | prohibited server data | 用户设备 | browser volatile memory | browser tab | 无 | N/A/律师确认 | 帧生命周期 | 无 | 无服务端副本 | Open |
| DPI-007 | landmarks/signals/calibration | 本地 tracker | 本地映射 | prohibited server data | 用户设备 | browser volatile memory | browser tab | 无 | N/A/律师确认 | 会话生命周期 | 无 | 无服务端副本 | Open |
| DPI-008 | operational event allowlist | 系统 | 可靠性/安全 | internal pseudonymous | TBD | logs TBD | eng/security | vendor TBD | 律师决定 | ≤30d 候选 | 内容无关 | TBD | Open |
| DPI-009 | optional analytics | 用户/系统 | 产品决策 | pseudonymous | TBD | 默认关闭 | product/privacy | vendor TBD | 律师决定 | D-012 | 默认无 | withdrawal/delete | Disabled |
| DPI-010 | deletion tombstone/proof | 系统 | 防恢复/审计 | content-free audit | TBD | durable DB | privacy/platform | cloud TBD | 律师决定 | TBD | 必须恢复 | TBD | Open |

## 必填检查

- 每个 store、region、subprocessor、recipient 和 admin/support 路径；
- 账号、内容、内容衍生元数据、日志、安全、支持和浏览器本地数据；
- retention 触发点、最早期限、provider 删除和失败重试；
- backup、restore、tombstone、crypto-erasure；
- exceptional access、legal hold 和 incident evidence；
- consent/objection/withdrawal/DSR UX；
- DPIA 或高风险筛查记录；
- 分析关闭时完整功能验证。
