# 第三方许可与通知登记

- **状态**：模板；未知条款 = 禁止进入产品
- **说明**：这是工程/采购记录，不替代法律意见。

| ID | 类型 | 名称/版本/不可变摘要 | 来源 | 用途 | 代码条款 | 权重条款 | 数据/训练来源条款 | 商用/SaaS | 再分发 | 编辑器/自动测试 | 通知/源码义务 | 安全维护 | 责任人/复核日 | 替换/回滚 | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| TP-001 | candidate model | See-through code `58a1cb11d13f85acec9bbddb8cd4b6487843d4cf`; exact model revisions and file digests in `spikes/gate_f_runner/model_profiles/see-through-v3-nf4.json` | [GitHub](https://github.com/shitagaki-lab/see-through) / pinned Hugging Face repositories in profile | 本地可丢弃 Gate F 语义拆层/补全候选 | Apache-2.0 | full LayerDiff 标记 Apache-2.0；所选 NF4/Marigold revision 元数据不完整，仍待确认 | 上游未提供足够逐数据集权利链；训练管线涉及另行许可素材 | 未准入产品/SaaS | 禁止项目再分发权重 | N/A | 代码保留 Apache notices；权重义务待确认 | 活跃；无隔离边界、仅限本机；Safetensors、固定摘要 | Local spike / 2026-07-23 | 固定区域/轻量分割基线 | Allowed for disposable local evaluation only; production/redistribution blocked |
| TP-008 | candidate model | anime-face-detector `v0.1.0` code `7db835de7a3a052eb4d68d241ae9f2cf28a0b509`; exact model revisions and file digests in `spikes/gate_f_runner/model_profiles/hysts-anime-face-v0.1.0.json` | [GitHub](https://github.com/hysts/anime-face-detector) / pinned Hugging Face repositories in profile | 本地 anime face/28 landmark proposal | MIT；vendored inference path Apache-2.0 | exact YOLOv3 与 HRNetV2 model cards 标记 MIT | 上游不保证原始训练数据 provenance | 仅本地可丢弃评估；产品准入未决定 | 许可允许并保留 notices；项目当前不分发权重 | N/A | 保留 MIT、Apache notices | 2026-07 活跃；固定 Safetensors，不用 mutable auto-download | Local spike / 2026-07-23 | fixed-region anchors / detector unavailable | Allowed for disposable local evaluation only |
| TP-002 | PSD writer | TBD | TBD | 分层 PSD | TBD | N/A | N/A | TBD | TBD | TBD | TBD | TBD | TBD | 替换库/重新立项 | Blocked |
| TP-003 | PSD reader | TBD | TBD | 独立验证 | TBD | N/A | N/A | TBD | TBD | TBD | TBD | TBD | TBD | 替换库 | Blocked |
| TP-004 | browser tracker | MediaPipe Tasks Vision `0.10.35` npm integrity `sha512-HOvadw.../egg==` + Face Landmarker float16/1 SHA-256 `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`; Kalidokit `1.1.5` npm integrity `sha512-cLaPf...0W1Q==`; full file digests in `spikes/gate_f_runner/gui/vendor/README.md` | [MediaPipe](https://github.com/google-ai-edge/mediapipe) / [Kalidokit](https://github.com/yeemachine/kalidokit) / pinned npm and Google model URLs | 浏览器内本地 478 点追踪、Live2D 参数求解与摄像头预览 | MediaPipe Apache-2.0；Kalidokit MIT | Face Landmarker 固定模型；独立模型再分发审查未完成 | 独立模型训练数据 provenance 未作为产品证据确认 | 仅本地可丢弃评估；产品/SaaS 准入阻止 | 当前仓库本地副本保留 notices；产品分发阻止 | N/A | 保留 Apache-2.0、MIT notices 和固定摘要 | MediaPipe 活跃；Kalidokit API 稳定但需安全复核 | Local spike / 2026-08-24 | 手动参数控制/关闭摄像头视图 | Allowed for disposable local evaluation only; production/redistribution blocked |
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
