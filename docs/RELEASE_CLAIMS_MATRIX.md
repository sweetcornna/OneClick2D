# 外部声明证据矩阵

- **状态**：模板；没有证据和批准不得发布声明。

| ID | 精确声明文本 | 产品/版本 | 地区/受众 | 关联需求 | 证据/样本/环境 | 已知限制 | 批准者 | 复核/到期触发 | Kill switch/撤回 | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|
| CL-001 | “自动生成受限范围的 2D 角色初稿” | TBD | TBD | FR-005/NFR-001 | Gate F/G4 | 仅声明输入范围 | TBD | model/scope change | 停止生成/改文案 | Blocked |
| CL-002 | “导出 `.oc2d` 与分层 PSD” | TBD | TBD | FR-015–017 | conformance/editor matrix | PSD 无绑定语义 | TBD | format/editor change | 关闭导出 | Blocked |
| CL-003 | “摄像头由产品在浏览器本地处理” | TBD | TBD | FR-012/NFR-006 | application-origin network/storage tests | 浏览器/OS 等外部边界 | TBD | dependency/origin change | 关闭 webcam | Blocked |
| CL-004 | “客户内容不用于训练或评估” | TBD | TBD | NFR-006 | data flow/access/audit | 异常法律路径需单独措辞 | TBD | purpose/access change | 停止上传 | Blocked |
| CL-005 | “服务端副本按声明期限删除” | TBD | TBD | FR-019 | 用户请求 15m；自动撤销 deadline 后 15m inventory-clear 的 deletion/restore drill | 本地下载不受控；失败显示 retrying | TBD | provider/store change | 停止上传 | Blocked |

每条声明必须有精确版本、地区、证据、批准、有效期和撤回机制。性能、质量、兼容、可访问性、安全和法律合规声明同样纳入。
