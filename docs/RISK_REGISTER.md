# 风险登记

- **状态**：每周及每个 Gate 复核
- **优先级**：Critical/High/Medium；缺 owner 的 Critical 阻止依赖工作。

| ID | 级别 | 风险/指标 | 缓解 | 预案 | Owner/Gate |
|---|---|---|---|---|---|
| R-001 | Critical | 单图自动假设失败；Gate F 自动路径低成功或不优于简单基线 | 权利真实分层、简单对照、硬原作约束、timebox | 分层输入/用户锚点/刚体/减少参数/停止 | ML / F |
| R-002 | Critical | 数据/模型权利或供给不足 | 预算、合同、台账、供应商候选、下架 | 缩小学习能力、延后或停止 | Product/Legal / 0 |
| R-003 | Critical | CIR 欠定义/摘要循环/两渲染器不一致 | v0.1 丢弃、v0.2 最小 ABI、非循环摘要、真实 fixture | 冻结 consumer，继续简化 | Format / 1 |
| R-004 | Critical | 复杂发型/遮挡导致身份改变或接缝 | eligibility、bounded reveal、原作保护、修正 | block 风格/减小运动 | ML / F–4 |
| R-005 | Critical | 网格/绑定反向、翻转、孔洞 | 最小参数注册、canonical suite、clamp、拓扑验证 | 修改强制集合需重新立项 | Graphics / F–2 |
| R-006 | Critical | 小团队无法同时交付研究/平台/合规 | 可行性先行、无 physics/offline、容量计划 | 延期/缩范围，不削硬门 | Program / 0 |
| R-007 | Critical | PSD 库/编辑器/许可破坏双输出 | 早期 exact-version spike、independent reader/golden | 暂停并重新立项，非静默单输出 | Export / F–1 |
| R-008 | Critical | 租户/下载/日志/浏览器/备份泄露艺术 | scope、proxy download、no-store、no-backup catalog、tests | 停止上传/下载、事故响应 | Security / 1–4 |
| R-009 | Critical | 摄像头声明超过可控边界或出现 trace 字段 | 无端点/字段、CSP、network/storage test、精确措辞 | 关闭 webcam，保留 manual | Privacy / 1–4 |
| R-010 | Critical | 删除、hold 和人工访问说法冲突 | 期限优先、异常政策/披露、inventory proof | 禁用 hold/access 或阻止外测 | Privacy/Legal / 0–3 |
| R-011 | High | 上传可重放或下载不可撤销 | create-only/finalize/proxy | 删除受影响直传 | Platform / 1 |
| R-012 | High | PNG/JPEG/ZIP/CIR/PSD/model 解析攻击 | 隔离、limits、安全格式、fuzz | 禁用/缩窄 parser | Security / 1 |
| R-013 | High | 小样本/切片/锁定集污染夸大质量 | 预注册 Wilson、group split、missing=fail、暴露要求 | 限制声明/扩样/失败 Gate | QA / 2–4 |
| R-014 | High | GPU OOM/重试/队列/成本失控 | CPU preflight、reserve、quota、双成本、threshold ladder | 拒绝新 start/暂停扩张 | Platform / 1–4 |
| R-015 | High | 地区、品牌、AI 透明决策晚阻塞 | launch readiness、临时代号、claims matrix | 更名/标记/限地区/延期 | Legal / 0–3 |
| R-016 | High | 第三方代码/权重/数据/编辑器条款不兼容 | immutable register、separate rights review | 隔离/替换/重测 | Legal/Security / 0–4 |
| R-017 | High | “一键/可编辑”造成专业级误解 | 自动初稿、形成研究、可见不确定性、修正 | 重新定位/缩范围 | Product / 1–4 |
| R-018 | High | 格式演进/服务结束困住用户 | 最小语义、current-1、copy migration、支持期决策 | 转换器/reader/deprecation | Format / 1–4 |
| R-019 | High | DB 恢复复活内容关联元数据 | no-backup catalog 或 crypto-erasure、tombstone、restore drill | 停止生成并 purge/reconcile | Privacy / 1–4 |
| R-020 | High | 运营 cause 与质量 finding 混乱 | 单注册表双轴、mapping tests | fail closed、修 registry | Platform/Product / 1 |

## 接受与升级

不可豁免：跨租户、摄像头边界、客户内容二次使用、损坏验证下载、删除、未知权利、关键/高危安全、原作像素保护和专有格式边界。其他残余风险接受需记录证据、范围、控制、kill switch、期限和产品/技术/领域批准。

新 impact-5/Critical 或指标触发在一个工作日内评审。关闭风险保留历史和证据，不删除。
