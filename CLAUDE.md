# CLAUDE.md

## 仓库状态与命令

OneClick2D 的确定性产品路径已在 `oneclick2d/` 实现，**仅用 Python 标准库**（PNG/JPEG 编解码、PSD 读写、ZIP 打包、JSON Schema 校验全部自研）。这是刻意选择：纯标准库让产品路径可运行而不预判 D-005，因此**不要**为它引入框架、第三方依赖或新的 build/dev 命令；任何依赖或架构决策仍须先关闭 OPEN_DECISIONS.md 的对应决策并写 ADR。Gate F 仍未评估，生产就绪仍需 Gate 1 及后续证据。

产品路径的能力边界必须如实陈述：语义拆层由确定性解剖学布局先验提出，本体状态一律报 `LOW_CONFIDENCE`（布局先验只定位区域，不识别解剖结构）；有限补全是边缘延展，不是生成模型；Gate F 前没有校准数据集，故全部 `confidence_facts.score` 与 `threshold_band` 必须为 `unavailable`，不得编造分数；模型驱动的 provenance 在缺少不可变 model/weights 摘要与权利登记记录前必须硬失败。ML 可提议 suitability/mask/landmark/depth/有限隐藏像素，确定性代码始终保留策略、本体、左右、拓扑、参数范围、插值、验证、渲染、回退与发布的决定权。

产品路径固定命令：

```bash
python -m unittest discover -s tests -p "test_*.py"
python scripts/check_registry_mirrors.py
python -m oneclick2d registries --show-parameters
python -m oneclick2d generate --source /path/to/cut-out-subject.png --output out/
python -m oneclick2d verify --package out/<name>.oc2d --psd out/<name>.psd
```

`generate` 要求已抠背景输入（语义层只覆盖角色；整幅不透明输入会把背景计入应覆盖区域，并以 `INPUT_BACKGROUND_NOT_SEPARATED` 警告记录），信封为 FR-001 的单边 1,024–8,192 px、≤40 MP、≤25 MiB。`verify` 必须由独立读取器重开归档、重算摘要、重跑语义验证，并按面板反序重合成 PSD 与 CIR 中性结果逐像素比对；两产物绑定同一 `project_payload_sha256` 才允许发布，PSD 失败必须阻断发布，不得静默降级为仅 `.oc2d`。`registries/*.json` 是被 CIR 摘要引用的权威镜像，YAML 只用于评审，改任一侧都要跑 `check_registry_mirrors.py`。

Gate F 预研的固定命令（`spikes/`，可丢弃，生产包不得导入）：

```bash
python scripts/validate_docs.py
python -m spikes.gate_f_runner smoke --run-id run.local-smoke
python -m spikes.gate_f_runner preflight --run-id run.local-technical
python -m spikes.gate_f_runner gui
python -m spikes.gate_f_runner model --source "/path/to/right-cleared.png" --run-id run.local-model
python -m spikes.gate_f_runner diagnose-fidelity --run-id run.local-model
python -m spikes.gate_f_runner motion --run-id run.local-model
python -m spikes.gate_f_runner model-candidate --run-id run.local-model
python -m spikes.gate_f_runner verify-model-candidate --run-id run.local-model
```

GUI 的模型模式和显式 `model` 命令应使用已抠背景、背景透明的角色图（通常为 PNG）；不透明背景会被源侧保真统计计为可见区域，而语义层通常只覆盖角色。该提示不是新的硬阻断，已抠背景也不保证通过中性保真门。文档检查只能称“立项文档 lint”；固定 smoke 只能称“标准库合成编排 smoke”；真实 raster Adapter 只能证明其锁定 profile 下的非计分 ingest/normalization 边界；`preflight` 只证明 purpose-created candidate/comparator、共享序列/renderer、paired evaluator 和 PSD structural readback 的本地技术预检，成功状态必须写 `LOCAL_TECHNICAL_PREFLIGHT_PASS` 且 `GATE_F_NOT_EVALUATED`；`gui` 只能是 loopback-only 本地图片工作台，可接收权利明确的本地 PNG/JPEG，并显式选择固定区域 deterministic baseline，或在同一规范化边界后调用无隔离边界、仅限本机的原生 Linux worker 的 hash-pinned See-through V3 NF4 模型路径，不得扩成外网或生产服务。模型执行及全部产物校验成功前必须保持 `model_used: false`。GUI 不生成 `.oc2d`、不自动删除工作区，成功只能写 `LOCAL_WORKBENCH_COMPLETED` 且 `GATE_F_NOT_EVALUATED`。显式 `model` 命令使用同一固定 profile，成功只能写 `LOCAL_MODEL_SPIKE_COMPLETED` 且 `GATE_F_NOT_EVALUATED`。`diagnose-fidelity` 只读诊断已完成运行的中性保真漏失，不修改运行产物、不是验收门且不改变任何阈值；成功只写 `LOCAL_FIDELITY_DIAGNOSIS_COMPLETED` 且 `GATE_F_NOT_EVALUATED`，不证明模型质量、蒙版语义、隐藏区域真实性或任何 Gate F 结论。`motion` 仍按原阈值计算并记录 active v6 运行的中性保真门，但门未通过不再阻断；此时 `quality.review_items` 必须追加含三项实测值与门限的 `FIDELITY_GATE_NOT_PASSED`，模型 profile 身份不匹配仍须硬失败。成功只能写 `LOCAL_MODEL_MOTION_DRAFT_COMPLETED` 且 `GATE_F_NOT_EVALUATED`；其 37 帧 bbox quad/affine 结果只能标 `research_draft`，不得描述为专业绑定、Live2D 成品、mesh-delta、`.oc2d` 或 `.moc3`。candidate v0.3 报告没有等价自由扩展点，因此 `model-candidate` 与 `verify-model-candidate` 仍只允许消费中性保真通过的受检 active v6 与 motion 结果，生成或核对单项 ontology/provenance/comparator arm-parity 预检。二者成功只能写 `LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED` 且 `GATE_F_NOT_EVALUATED`，不得生成或声称 ballot、paired outcome、`F-USABLE` 或 20 项 Gate F 结果。supporting weight 许可元数据仍不完整，禁止权重再分发、禁止权重入库、禁止产品使用。原生模型运行时不存在安全隔离边界；它们也都不是 schema/package conformance、模型质量、PSD 互操作或 Gate F 可行性证明。Gate F 前可执行预研放在 `spikes/`，任何生产包不得导入它；反向也禁止——`oneclick2d/` 不得导入 `spikes/`。技术栈选定后，同一变更更新 README、CONTRIBUTING 和本文件的固定命令。

默认模型 profile 为宿主中立的 `see-through.v3.nf4.1280.source-preserve.v6`；当前运行事实固定为 `runtime.kind = native-linux`、`runtime.isolation = none-host-local`、`runtime.isolation_notice = "无隔离边界、仅限本机"`。worker 必须同时逐字校验两个 isolation 字段，任一不符即拒绝加载，且 native profile 不得包含 `distribution`；`runtime.python_path_entries = ["common"]` 必须由受信探针实算 realpath 验证生效。v6 保留 v5 的固定语义 alpha 噪声底清理、按逐层深度把原图 RGB 回填到最前可见语义层，以及按清理后各层最大 alpha 重建中性图；每次运行使用一次性 challenge、源图 SHA-256 和产物清单摘要，把 attestation 绑定到本次运行与本次产物清单，并由受信父进程独立重算清单后校验。该绑定不证明被钉死的 entrypoint 确实执行过，不是密码学执行证明或可信执行环境保证；完全控制 worker 运行环境者仍可对自造产物计算自洽清单。报告必须记录输入证据、可见像素保真和 `review_required`，不得把该处理描述为蒙版语义正确、隐藏区域真实或商用级。未附加 motion 时网格/绑定/动态必须是 `not_generated`；附加后也只能把 bbox quad、五参数 affine binding 与动态预览标记为 `research_draft`。历史 `see-through.v3.nf4.1280.wsl2.v2`、`see-through.v3.nf4.1280.wsl2.source-preserve.v3`、`see-through.v3.nf4.1280.wsl2.source-preserve.v4` 与 `see-through.v3.nf4.1280.wsl2.source-preserve.v5` 结果继续按各自原 profile/入口摘要只读验证，且不得追溯声称获得 v6 语义或 v6 的运行/产物清单绑定。

## 产品硬边界

- OneClick2D 是未清理的内部代号；Live2D/Cubism 是第三方生态，不是本项目品牌。
- Gate F 先验证受限的正面二次元半身图 → 自动可动初稿；`oneclick2d/` 已实现该产品路径，但实现存在不等于 Gate F 通过或生产就绪，Ready 判定仍以 Gate 证据为准。
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
