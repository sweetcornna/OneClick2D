# Gate F：自动生成可行性预研

- **目的**：在建设生产系统前验证最核心的技术假设。
- **时间盒**：Gate 0 后 15 个工作日。
- **性质**：本地优先、可丢弃，不构成产品实现。

## 1. 必答问题

1. 能否从扁平图自动恢复足够的语义图层？
2. 有限补全能否在安全运动范围内避免明显孔洞和身份改变？
3. 简单刚体/仿射或最小 mesh-delta 绑定，哪种能以最低复杂度达到可用初稿？
4. 自动结果是否明显优于静态/简单剪纸基线？
5. 是否能够可靠写出并独立读取窄规范分层 PSD？
6. 运行时、显存和人工检查成本是否可接受？

## 2. 数据集

使用 20 个预注册、权利明确、带真实分层参考的素材：

- 来源于多个创作者和非重复角色家族；
- 覆盖简单/中等发型、饰品、透明边、不同服装和背景；
- 每个资产有权利台账，明确允许研发、评价和内部截图的范围；
- 真实分层、蒙版、关键点和参考绑定只能用作评分 oracle；
- 不允许客户上传、未确认来源或受限制素材。

在实验开始前锁定资产列表、分层、切片和评价规则。

## 3. 对照方案

必须使用不可变版本标识比较：

### 拆层

- 简单分割/规则基线；
- 候选语义拆层模型；
- 如采用 See-through，必须先完成代码、权重、依赖和数据条款审查。

### 补全

- 不补全；
- 接缝扩张/颜色填补；
- 候选学习式补全。

### 运动

- 静态图；
- 刚体/仿射剪纸；
- 最小 mesh-delta。

更复杂方案只有在预注册指标显著改善且不破坏安全、运行成本和编辑性时才可采用。

## 4. 自动路径协议

每个素材仅向系统提供扁平源图和统一配置，依次执行：

```text
标准化 → 自动适用性判断 → 自动语义拆层 → 自动有限补全
→ 自动网格/绑定 → 自动验证 → 中性及参数极值渲染 → PSD 写出
```

禁止在主实验路径中手工绘制蒙版、修正锚点、改网格、调整绑定或选择有利参数。手工资产仅作为对照和评分参考。

每次运行记录：代码提交、环境、模型/权重摘要、配置摘要、输入摘要、随机种子、运行时、峰值显存和所有中间输出摘要。

## 5. 最小能力候选

Gate F 不预先承诺产品宽参数集。Gate 0 必须在运行 20 项前冻结 Gate F 的评分能力集合；如比较多个能力 profile，必须同时冻结 profile 选择和通过规则。以下是待 Gate 0 选择的有限候选：

- 双眼同步或独立开合；
- 眼球 X/Y；
- 嘴部开合；
- 头部小范围 X/Y；
- 呼吸或身体小范围运动（二选一，可选）。

冻结的 Gate F 评分能力缺失时，该项不能计为 `F-USABLE`。Gate F 结果可以提出后续产品 mandatory/optional 注册表，但不能用同一 20 项事后选择评分集合；若选择规则未预注册，必须在另一个 untouched、group-disjoint 集上确认后才能 PASS。

## 6. 通过标准

20 个素材必须全部运行。技术选择已冻结在 [Gate F 技术预注册决策](gate-records/GATE_F_TECHNICAL_PREREGISTRATION.md)：固定 simple-cutout comparator、盲化 paired primary metric、净胜 margin、exact binomial 不确定性及 tie/missing rule。该决策须由 Gate 0 绑定不可变 tree，并在 D-003/D-009 关闭后才激活。通过要求：

- 至少 12/20 达到 [质量计划](QUALITY_PLAN.md) 的 `F-USABLE`；
- 自动路径按预注册 paired rule 优于 comparator；不满足时 PASS 不可用，只能 RECHARTER 或 STOP；
- 强制语义槽位存在率 ≥90%；
- 任一 n≥3 的预注册切片不能 0 成功；
- 可见原始像素在羽化容差外零改写；
- 输出网格全部通过有限值、索引和拓扑检查；
- 身份改变的脸部补全零通过；
- PSD 合成和顺序通过独立读取器及至少一个目标编辑器验证；
- 输出完整失败分类、运行时与显存分布、精确二项不确定性。

这是可行性证据，不是总体质量声明。

## 7. 失败和转向

若未通过，不得开始生产认证、分布式队列、云扩容或外部测试。立项委员会必须选择：

1. 要求输入分层 PSD；
2. 增加用户关键点/蒙版锚定；
3. 降为刚体/仿射剪纸；
4. 减少强制参数；
5. 改为引导式而非全自动；
6. 终止项目。

任何选择都要更新项目章程、需求、指标和外部表述。

## 8. 可丢弃编排骨架

仓库提供一个仅使用 Python 标准库、单进程同步执行的 `spikes/gate_f_runner`，用于预先验证 immutable run/stage spec、确定性 seed/digest、attempt-scoped candidate、原子 commit、合作式 cancel、资源记录和 typed manifest。它只运行 purpose-created numeric synthetic fixture，不生成图像、CIR、`.oc2d` 或 PSD。

Smoke 通过只证明合成字节编排正确，不回答第 1 节六个可行性问题。首个真实 `raster.normalize.pillow.v1` Adapter 仅验证锁定 Pillow 12.1.0 profile 下的本地 PNG/JPEG 接收、orientation/color 规范化和 metadata stripping，作为 candidate/comparator 共享的非计分 ingest preflight；它也不回答拆层、补全、绑定、渲染或 PSD 可行性。固定 `simple-cutout.comparator.pillow.v1` Adapter 可在同一 profile 下以 purpose-created raster 运行 12 帧 neutral/endpoint/combination 实现预检，但不含计分 seeded trajectory，也不证明 candidate renderer parity。实际 suitability/decomposition/completion/mesh/rig/candidate render/PSD adapters 和 20 项 paired scoring 尚未实现；不得把 synthetic、raster normalization 或 simple-cutout preflight stage、结果或 manifest 计入 20 项评分。

## 9. 预算与产出

Gate 0 必须批准人员、GPU 时数、素材/标注、PSD 编辑器席位和法律/许可审查预算。产出包括：

- 可重复运行的实验脚本；
- 锁定清单和资产权利台账；
- 逐素材结果与失败分类；
- 对照统计、视觉样例和资源指标；
- Gate F 决策记录；
- 继续、转向或终止建议。
