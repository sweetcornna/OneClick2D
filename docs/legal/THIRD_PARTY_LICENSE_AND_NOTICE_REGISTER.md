# 第三方许可与通知登记

- **状态**：模板；未知条款 = 禁止进入产品
- **说明**：这是工程/采购记录，不替代法律意见。

| ID | 类型 | 名称/版本/不可变摘要 | 来源 | 用途 | 代码条款 | 权重条款 | 数据/训练来源条款 | 商用/SaaS | 再分发 | 编辑器/自动测试 | 通知/源码义务 | 安全维护 | 责任人/复核日 | 替换/回滚 | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| TP-001 | candidate model | See-through / exact version TBD | upstream TBD | Gate F 拆层候选 | 待确认 | 待确认 | 待确认 | 待确认 | 待确认 | N/A | 待确认 | 待评估 | TBD | 简单分割基线 | Blocked |
| TP-002 | PSD writer | TBD | TBD | 分层 PSD | TBD | N/A | N/A | TBD | TBD | TBD | TBD | TBD | TBD | 替换库/重新立项 | Blocked |
| TP-003 | PSD reader | TBD | TBD | 独立验证 | TBD | N/A | N/A | TBD | TBD | TBD | TBD | TBD | TBD | 替换库 | Blocked |
| TP-004 | browser tracker | TBD | TBD | 本地摄像头预览 | TBD | TBD | TBD | TBD | TBD | N/A | TBD | TBD | TBD | 手动控制 | Blocked |
| TP-005 | exact Photoshop | TBD | vendor | 兼容性烟雾测试 | service/EULA | N/A | N/A | TBD | N/A | 席位/机器/自动化权利 | TBD | vendor | TBD | 无法验证则重新立项 | Blocked |
| TP-006 | exact Krita | TBD | upstream | 兼容性烟雾测试 | TBD | N/A | N/A | TBD | TBD | TBD | TBD | TBD | TBD | 替换版本 | Blocked |
| TP-007 | spike image library | Pillow 12.1.0 / PyPI CPython 3.14 win_amd64 wheel SHA-256 `4f9f6a650743f0ddee5593ac9e954ba1bdbc5e150bc066586d4f26127853ab94` | [PyPI](https://pypi.org/project/pillow/12.1.0/) / [source tag](https://github.com/python-pillow/Pillow/tree/12.1.0) | 本地可丢弃 PNG/JPEG 接收/规范化 | MIT-CMU；保留版权和许可通知；名称宣传限制；无担保 | N/A | 软件许可不授予模型/数据/素材权利 | 代码许可允许；产品准入仍未决定 | 允许，须携 notices | N/A | 分发副本保留 LICENSE/版权/许可；supporting docs 包含 notices | 12.1.x 安全维护需复核；严格 pin 12.1.0 | Spike owner / 2026-08-22 | 删除 Adapter/回退 synthetic smoke | Allowed for disposable local spike only |
## 准入规则

1. 软件许可、模型权重、训练数据、字体/素材、托管服务和编辑器测试权利分别审查；
2. mutable alias 不可作为发布身份；
3. unknown、non-commercial、research-only、field-of-use 或不兼容条款阻止使用；
4. 记录必要通知、源码提供、专利、署名和输出内通知；
5. 每项具备下架、替换、重测和回滚路径；
6. 供应商确认和法律意见链接只存受控位置，本文保存不敏感结论和责任人。
