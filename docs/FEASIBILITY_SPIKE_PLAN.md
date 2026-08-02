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

Smoke 通过只证明合成字节编排正确，不回答第 1 节六个可行性问题。首个真实 `raster.normalize.pillow.v1` Adapter 仅验证锁定 Pillow 12.1.0 profile 下的本地 PNG/JPEG 接收、orientation/color 规范化和 metadata stripping，作为 candidate/comparator 与模型 GUI 共享的非计分 ingest preflight；它也不回答拆层、补全、绑定、渲染或 PSD 可行性。固定 `simple-cutout.comparator.pillow.v1` Adapter 可在同一 profile 下以 purpose-created raster 运行共享的 12 帧 neutral/endpoint/combination 前缀和 25 帧显式 seed 定点轨迹，但尚不证明 candidate renderer parity。purpose-created deterministic candidate baseline 已覆盖固定 suitability、required-slot layer proposal、有限 fill、quad geometry、全轨迹验证和共享 renderer；标准库窄 PSD writer/独立 strict reader 只完成无 ICC、无外部编辑器证据的结构预检；paired evaluator 已覆盖 arm parity、盲化、仲裁、失败规则和精确统计，但没有真实 reviewer ballot 或 20 项素材结果；`preflight` 命令把这些 purpose-created 路径、paired statistics fixture 和 PSD structural readback 组装成 checksummed bundle；其成功状态只能是 `LOCAL_TECHNICAL_PREFLIGHT_PASS` 与 `GATE_F_NOT_EVALUATED`。loopback-only `gui` 另提供本地图片工作台，可显式选择固定区域 deterministic baseline，或将规范化 PNG 交给隔离 WSL2 worker 的固定 See-through V3 NF4 profile。模型 GUI 只放行固定模型身份、重建图、受清单约束的语义 RGBA/深度中间图和受检 PSD；只有模型进程、清单与 PSD 校验全部成功才记录 `model_used: true`，否则保持 `false`。该路径不生成 `.oc2d`、不接收外部用户上传，结果只能称 `LOCAL_WORKBENCH_COMPLETED` 与 `GATE_F_NOT_EVALUATED`。显式 `model` 命令使用同一 hash-pinned profile，成功只能称 `LOCAL_MODEL_SPIKE_COMPLETED` 与 `GATE_F_NOT_EVALUATED`；其成功结果可被 GUI 只读导入。supporting weight 许可元数据仍不完整，不分发权重、不用于产品或 20 项计分。模型输出仍只是 semantic/inpainting proposal。单项 `model-candidate` technical preflight 可在受检 active v5 与 motion 结果上确定性映射完整 ontology、character-anatomical left/right、source-visible/生成区 provenance，并以同一 canonical raster、premultiplied renderer 与 37 帧 identity 运行固定 comparator；`verify-model-candidate` 从磁盘重算全部证据。它只证明该单项桥接契约与 arm parity，成功只能是 `LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED` / `GATE_F_NOT_EVALUATED`，不得生成 reviewer ballot、paired outcome、`F-USABLE` 或 20 项 denominator，也不证明动态逐帧 source-pixel 硬门、语义/补全质量、外部 PSD 互操作或 `.oc2d`。不得把 synthetic、raster normalization、candidate/comparator preflight stage、GUI 工作台、单项模型结果、model-candidate preflight 或 manifest 计入 20 项评分。

默认 `source-preserve.v5` profile 保留 v4 的确定性 alpha 净化与 visible-source RGB 回填：先把每层不高于 31/255 的 alpha 清零并线性重映射其余区间，再逐像素以清理后的 alpha 和深度选出最前层，把规范化输入 RGB 写入该层，隐藏层保留模型生成像素，并以各层最大清理 alpha 重建中性图，避免低置信度背景跨层累积。v5 还以一次性 challenge、源图 SHA-256 和产物清单摘要把 attestation 绑定到本次运行与本次产物清单，受信父进程独立重算后校验；该绑定不证明被钉死的 entrypoint 确实执行过，不是密码学执行证明或可信执行环境保证，完全控制 WSL2 worker 环境者仍可对自造产物计算自洽清单。报告计算可见像素完全一致率和 RGB 平均误差，但整体状态仍为 `review_required`；该证据不关闭语义蒙版、遗漏部件、遮挡补全、外部 PSD 互操作或 `.oc2d` 缺口。旧 `v2`、`source-preserve.v3` 与 `source-preserve.v4` 结果按各自原 profile 和入口摘要兼容读取，不得追溯声称应用 v5 的本次运行/本次产物清单绑定。

可选 `motion` 命令只接受中性保真通过的 active v5 本地运行，把非空语义层 alpha 包围盒转成单四边形，以头部 X/Y、双眼开合和嘴部开合五个 affine 参数运行同一固定 37 帧序列。输出报告重新校验帧摘要、参数、绑定、正面积几何和中性重建误差，GUI 可逐帧或播放诊断孔洞、接缝和语义错误。该结果始终为 `review_required` / `research_draft`，不是 mesh-delta、专业绑定、`.oc2d`、`.moc3`、20 项评分结果或 Gate F 可行性证据。

## 9. 预算与产出

Gate 0 必须批准人员、GPU 时数、素材/标注、PSD 编辑器席位和法律/许可审查预算。产出包括：

- 可重复运行的实验脚本；
- 锁定清单和资产权利台账；
- 逐素材结果与失败分类；
- 对照统计、视觉样例和资源指标；
- Gate F 决策记录；
- 继续、转向或终止建议。
