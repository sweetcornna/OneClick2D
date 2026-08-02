# CLAUDE.md

## 仓库状态与命令

OneClick2D 处于文档优先的立项/可行性阶段。Gate F 尚未证明核心技术假设，也没有固定生产 Python/Node/GPU 栈。不要擅自添加框架或编造 build/dev 命令；先关闭 OPEN_DECISIONS.md 的对应决策并写 ADR。

固定文档 lint、单元测试和 synthetic smoke 只要求 Python 标准库；真实 raster Adapter 和固定 simple-cutout comparator 的 Pillow 12.1.0 仅是 hash-pinned disposable spike 依赖，不是生产技术栈决策：

```bash
python scripts/validate_docs.py
python -m unittest discover -s tests -p "test_*.py"
python -m spikes.gate_f_runner smoke --run-id run.local-smoke
python -m spikes.gate_f_runner preflight --run-id run.local-technical
python -m spikes.gate_f_runner gui
python -m spikes.gate_f_runner model --source "C:/path/to/right-cleared.png" --run-id run.local-model
python -m spikes.gate_f_runner motion --run-id run.local-model
python -m spikes.gate_f_runner model-candidate --run-id run.local-model
python -m spikes.gate_f_runner verify-model-candidate --run-id run.local-model
```

文档检查只能称“立项文档 lint”；固定 smoke 只能称“标准库合成编排 smoke”；真实 raster Adapter 只能证明其锁定 profile 下的非计分 ingest/normalization 边界；`preflight` 只证明 purpose-created candidate/comparator、共享序列/renderer、paired evaluator 和 PSD structural readback 的本地技术预检，成功状态必须写 `LOCAL_TECHNICAL_PREFLIGHT_PASS` 且 `GATE_F_NOT_EVALUATED`；`gui` 只能是 loopback-only 本地图片工作台，可接收权利明确的本地 PNG/JPEG，并显式选择固定区域 deterministic baseline，或在同一规范化边界后调用隔离 WSL2 worker 的 hash-pinned See-through V3 NF4 模型路径，不得扩成外网或生产服务。模型执行及全部产物校验成功前必须保持 `model_used: false`。GUI 不生成 `.oc2d`、不自动删除工作区，成功只能写 `LOCAL_WORKBENCH_COMPLETED` 且 `GATE_F_NOT_EVALUATED`。显式 `model` 命令使用同一固定 profile，成功只能写 `LOCAL_MODEL_SPIKE_COMPLETED` 且 `GATE_F_NOT_EVALUATED`。`motion` 只接受通过中性保真校验的 active v4 运行，成功只能写 `LOCAL_MODEL_MOTION_DRAFT_COMPLETED` 且 `GATE_F_NOT_EVALUATED`；其 37 帧 bbox quad/affine 结果只能标 `research_draft`，不得描述为专业绑定、Live2D 成品、mesh-delta、`.oc2d` 或 `.moc3`。`model-candidate` 只允许消费受检 active v4 与 motion 结果，生成单项 ontology/provenance/comparator arm-parity 预检；`verify-model-candidate` 必须独立重算。二者成功只能写 `LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED` 且 `GATE_F_NOT_EVALUATED`，不得生成或声称 ballot、paired outcome、`F-USABLE` 或 20 项 Gate F 结果。supporting weight 许可元数据仍不完整，禁止权重再分发和产品使用。它们都不是 schema/package conformance、安全隔离、模型质量、PSD 互操作或 Gate F 可行性证明。Gate F 前可执行预研放在 `spikes/`，任何生产包不得导入它。技术栈选定后，同一变更更新 README、CONTRIBUTING 和本文件的固定命令。

默认模型 profile 为 `see-through.v3.nf4.1280.wsl2.source-preserve.v4`。它只在 PSD 组装前以固定噪声底清理语义 alpha、按逐层深度把原图 RGB 回填到最前可见语义层，并以清理后各层最大 alpha 重建中性图；报告必须记录输入证据、可见像素保真和 `review_required`。不得把该处理描述为蒙版语义正确、隐藏区域真实或商用级。未附加 motion 时网格/绑定/动态必须是 `not_generated`；附加后也只能把 bbox quad、五参数 affine binding 与动态预览标记为 `research_draft`。历史 `see-through.v3.nf4.1280.wsl2.v2` 与 `see-through.v3.nf4.1280.wsl2.source-preserve.v3` 结果继续按各自原 profile/入口摘要验证，且不得追溯声称应用 v4 蒙版净化。

## 产品硬边界

- OneClick2D 是未清理的内部代号；Live2D/Cubism 是第三方生态，不是本项目品牌。
- Gate F 先验证受限的正面二次元半身图 → 自动可动初稿；Gate F 前生产代码默认不可 Ready。
- 一期必须同时输出验证过的 `.oc2d` 和分层 PSD；取消任何一个要重新立项。
- 不实现、解析、检查、fixture、承诺或逆向 `.moc3`。Gate C 需要官方能力、许可/商标/法律和独立 ADR，且永不授权逆向。
- 产品是需检查/有限修正的自动初稿，不是专业绑定替代或隐藏内容真实恢复。

## 核心路径

```text
隔离接收 → 适用性策略 → 语义拆层 → 有限补全 → 图层合成
→ 确定性网格/最小绑定 → 全项目验证 → 预览编译
→ .oc2d + PSD → 独立复核
```

浏览器负责说明、检查/修正、手动预览、本地摄像头、下载/删除。控制面负责租户状态、幂等、attempt 所有权、删除和发布。Worker 只处理不可变 stage spec 和 attempt 前缀。Renderer 只消费验证 CIR；exporter 是只读投影。

ML 可提议 suitability、mask、landmark、depth、有限隐藏像素和置信；确定性代码负责策略、本体、左右、拓扑、参数/范围、插值、验证、渲染、回退及发布。

## 表示约定

- CIR 权威；PSD/preview buffer 非权威。
- 左上原点、X 右/Y 下、source pixel、UV 左上 `[0,1]`、角色解剖学左右；degree/second；sRGB image、linear mask、straight alpha。
- stable ID 不是数组位置/显示名；所有引用解析；要求的图无环。
- 生成区不能在羽化容差外改写可见原作；附 mask、seed/model/config provenance 和 confidence。
- `.oc2d` 无需模型/推理即可渲染，但一期不承诺应用离线启动。
- 任一权威修改创建 revision，旧 validation/ack/preview/export 失效。

## 阶段和契约

阶段包含 immutable input、attempt/contract ID、stable seed、全部输出影响摘要、原子验证 commit、cancel/progress、资源上限、类型化 outcome、scratch cleanup 和 egress policy。缓存键必须完整；晚到/重复工作不能覆盖发布结果。

先改 schema/spec/example，再生成类型、加正负/版本/迁移 fixture，再改 producer/consumer。严格拒绝 duplicate JSON key、NaN/Inf、非法 UTF-8、资源超限、路径攻击和无效 geometry。

## 隐私、内容和日志

OneClick2D 控制的代码没有摄像头帧/裁剪/关键点/嵌入/校准/表达信号的服务端端点、包字段或日志，并不主动传输/持久化。客户内容/衍生物不用于训练、校准、评估、人工质量审核或营销。分析默认关闭。

Git/CI/docs 只用权利明确 assets。禁止用户艺术、客户项目/PSD、摄像头数据、生产日志/路径、秘密、私有数据和未批准权重。日志只允许 opaque ID、reason code、version、时间/资源桶；禁止内容、文件名/路径/URL、精确 art hash、free text 和 camera 字段。

## 测试和评估

确定性测试不能替代 Gate F 或冻结 benchmark。模型变更需不可变模型/数据/config、group split、校准/切片、资源、prior comparison 和权利证据。几何/绑定/渲染运行中性、极值、组合和 seeded trajectory，检查 finite/index/topology/sign/seam/source-pixel。端到端覆盖 accept/review/block、P0 修正、revision、OOM、cancel/recovery、双 export/reopen、delete/restore、two-tenant 和 camera network/storage。

## 决策与范围控制

先读 docs/index.md、PROJECT_CHARTER、PRODUCT_REQUIREMENTS、FEASIBILITY_SPIKE_PLAN 和相关规范。架构、技术栈、公开格式/坐标、持久数据/网络/摄像头、模型/数据/许可、硬件声明和兼容性变更使用 ADR。任何待决实现是 disposable spike，除非 ADR 正式采用。
