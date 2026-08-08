# OneClick2D

> **状态：项目立项 / 可行性验证，尚未形成可发布产品。**

OneClick2D 是暂定的内部项目代号。项目探索把一张经过授权、接近正面的二次元半身立绘，自动转换为可检查、可有限修正、具有基础参数化运动能力的 2D 角色初稿。

## 一期目标

在明确限定的输入范围内完成：

1. 文件安全检查和角色适用性验证；
2. 语义拆层及有限的遮挡区域补全；
3. 网格、最小参数集和安全运动范围生成；
4. 浏览器手动参数预览；
5. 经独立验证的 `.oc2d` 可编辑项目及分层 PSD 导出；
6. 在另行验证后，提供仅由应用在浏览器本地处理的摄像头预览。

“一键”仅指用户执行一次生成操作，不代表无需检查、无需修正，也不承诺达到专业建模师成品水平。

## 明确边界

- 一期**不生成、解析、检查、逆向或承诺兼容** Live2D Cubism `.moc3`；
- Live2D/Cubism 是第三方产品及商标，本项目不暗示隶属、认可或合作；
- 一期不做全身、侧脸、多人、真人照片、重度遮挡和复杂团队协作；
- 用户上传内容及衍生物不用于训练、微调、校准、评估、人工质量审核、演示或营销；
- 产品代码不提供接收摄像头帧、裁剪、关键点、嵌入、校准样本或表情信号的服务端接口，也不主动传输或持久化这些数据；
- `.oc2d` 是项目自有的实验性格式；分层 PSD 只承载栅格图层，不承载绑定语义。

## 当前阶段

项目必须先通过 [Gate F 可行性预研](docs/FEASIBILITY_SPIKE_PLAN.md)，证明“扁平立绘 → 自动可动初稿”的核心假设，然后才进入生产化建设。

`oneclick2d/` 已实现完整的确定性产品路径（隔离接收 → 适用性策略 → 语义拆层 → 有限补全 → 图层合成 → 确定性网格/最小绑定 → 全项目验证 → 预览编译 → `.oc2d` + 分层 PSD → 独立复核），仅依赖 Python 标准库。它**不改变 Gate F 状态**：拆层由确定性解剖学布局先验提出、报告为 `LOW_CONFIDENCE`，补全是边缘延展而非生成模型，置信度一律为 `unavailable`；Gate F 仍未评估，生产就绪仍需 Gate 1 及后续证据。

文档入口见 [docs/index.md](docs/index.md)。开发和评审要求见 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [开发规范](docs/DEVELOPMENT_STANDARDS.md)。

## 产品路径固定命令

```bash
python -m unittest discover -s tests -p "test_*.py"
python scripts/check_registry_mirrors.py
python -m oneclick2d registries --show-parameters
python -m oneclick2d generate --source /path/to/cut-out-subject.png --output out/
python -m oneclick2d verify --package out/project.local.revision.0001.oc2d --psd out/project.local.revision.0001.psd
```

`generate` 要求**已抠背景**的 PNG/JPEG：语义层只覆盖角色，若输入整幅不透明，背景会被计入应覆盖区域，`INPUT_BACKGROUND_NOT_SEPARATED` 会以警告记录。尺寸信封为 FR-001 的单边 1,024–8,192 px、总像素 ≤40 MP、文件 ≤25 MiB。栅格化是纯标准库实现，1024×1024 单次运行需要数分钟。

`verify` 由**独立读取器**重新打开归档、重算全部摘要、重跑语义验证，并按面板反序重合成 PSD 与 CIR 中性结果逐像素比对。两个产物必须绑定同一 `project_payload_sha256` 才允许发布；PSD 失败会阻断发布，不会静默降级为仅 `.oc2d`。

## 当前检查与预研烟雾测试

以下固定命令中的 active v6 模型支持命令在原生 Linux 宿主运行，并使用锁定的 CPython/Pillow 12.1.0 环境；`model --source` 必须传本机 POSIX 路径（例如 `/path/to/right-cleared.png`）。该 runtime 无隔离边界、仅限本机；历史 v4/v5 WSL2 profile 只用于按原身份只读验证，不是 active 操作步骤。

GUI 的模型模式和显式 `model` 命令应使用已抠背景、背景透明的角色图（通常为 PNG）。不透明背景会被源侧保真统计计为可见区域，而语义层通常只覆盖角色；这是输入前提提示，不是新的硬阻断，已抠背景也不保证通过中性保真门。

```bash
python scripts/validate_docs.py
python -m unittest discover -s tests -p "test_*.py"
python -m spikes.gate_f_runner smoke --run-id run.local-smoke
python -m spikes.gate_f_runner preflight --run-id run.local-technical
python -m spikes.gate_f_runner gui
python -m spikes.gate_f_runner model --source "/path/to/right-cleared.png" --run-id run.local-model
python -m spikes.gate_f_runner diagnose-fidelity --run-id run.local-model
python -m spikes.gate_f_runner motion --run-id run.local-model
python -m spikes.gate_f_runner model-candidate --run-id run.local-model
python -m spikes.gate_f_runner verify-model-candidate --run-id run.local-model
```

前两项是立项文档 lint 和单元测试；Pillow 相关测试仅在依赖存在时运行。第三项仅验证可丢弃的标准库本地 Gate F 编排骨架能以不可变输入、确定性 seed 和 attempt 输出生成 typed manifest。第四项运行 purpose-created candidate/comparator、共享 37 帧序列与 renderer、paired statistics fixture、PSD structural readback，并生成 checksummed bundle；成功只称 `LOCAL_TECHNICAL_PREFLIGHT_PASS`，始终是 `GATE_F_NOT_EVALUATED`。第五项在 `127.0.0.1:8765` 启动本地图片工作台，可显式选择固定区域 deterministic baseline，或先经锁定 Pillow 规范化再调用无隔离边界、仅限本机的原生 Linux worker 的 See-through V3 NF4 模型路径；模型页展示固定模型身份、输入/重建、受清单约束的语义 RGBA/深度中间图和受检 PSD。模型执行及全部产物校验成功前始终记录 `model_used: false`，成功后才记录 `true`。第六项是同一固定模型 profile 的显式 CLI 预研入口；第七项 `diagnose-fidelity` 只读诊断已完成运行的中性保真漏失，不修改运行产物、不是验收门且不改变任何阈值；成功只写 `LOCAL_FIDELITY_DIAGNOSIS_COMPLETED` 且 `GATE_F_NOT_EVALUATED`，不证明模型质量、蒙版语义、隐藏区域真实性或任何 Gate F 结论。第八项对受检 active v6 运行生成 37 帧语义 bbox quad/affine 动态研究初稿；中性保真门仍按原阈值计算和记录，但未通过时不再阻断 motion，而在 `quality.review_items` 追加含 coverage、exact ratio、RGB MAE 实测值与门限的 `FIDELITY_GATE_NOT_PASSED`，profile 身份不匹配仍硬失败。第九项把该受检模型与 motion 结果确定性映射到完整 ontology、解剖学左右、source-visible/生成区 provenance，并在同一 canonical raster、renderer 和 37 帧身份下运行固定 comparator；第十项从磁盘重新计算并严格核对全部证据。candidate v0.3 报告没有等价自由扩展点，故这两条 candidate 路径仍要求中性保真通过。两项成功都只能称 `LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED` 与 `GATE_F_NOT_EVALUATED`，不会生成 ballot、paired outcome 或 `F-USABLE`。所选 supporting weight 许可元数据仍不完整，因此禁止权重再分发、禁止权重入库、禁止产品使用或 Gate F 计分。结果写入被 Git 忽略的 `workspaces/gate-f-spike/`，不自动删除；GUI 路径只能称 `LOCAL_WORKBENCH_COMPLETED`，模型命令只能称 `LOCAL_MODEL_SPIKE_COMPLETED`，motion 命令只能称 `LOCAL_MODEL_MOTION_DRAFT_COMPLETED`；这些本地路径均不生成 `.oc2d`、不提供外网端点，且都是 `GATE_F_NOT_EVALUATED`。

原生模型运行时不存在安全隔离边界；这些命令也不证明 JSON Schema/package conformance、模型质量、专业拆层/补全/绑定、PSD 互操作或 Gate F 可行性。

当前默认模型 profile 为宿主中立的 `see-through.v3.nf4.1280.source-preserve.v6`，运行时为 `native-linux`，`runtime.isolation = none-host-local`，固定声明为“无隔离边界、仅限本机”。它保留 v5 的低置信度 alpha 清理、保留区间线性重映射、最前可见语义层原图 RGB 回填、中性重建规则，以及每次运行的一次性 challenge、源图 SHA-256 和产物清单摘要绑定；受信父进程会独立重算并核对清单。该绑定不证明被钉死的 entrypoint 确实执行过，不是密码学执行证明或可信执行环境保证；完全控制 worker 运行环境者仍可对自造产物计算自洽清单。GUI 同时展示输入/重建对照、可见像素保真指标和能力矩阵；模型结果始终是 `review_required`。未运行 `motion` 时网格、参数绑定和动态预览保持 `not_generated`；运行后仅把 bbox quad、五参数 affine binding 和动态预览标为 `research_draft`，不生成 mesh-delta、`.oc2d` 或 `.moc3`。历史 `see-through.v3.nf4.1280.wsl2.v2`、`see-through.v3.nf4.1280.wsl2.source-preserve.v3`、`see-through.v3.nf4.1280.wsl2.source-preserve.v4` 与 `see-through.v3.nf4.1280.wsl2.source-preserve.v5` 模型结果继续按各自原 profile/入口摘要只读验证，不得追溯声称获得 v6 语义或 v6 的运行/产物清单绑定。

## 许可

仓库代码、模型、数据、素材和贡献许可尚未决策。在 [D-002](docs/OPEN_DECISIONS.md) 关闭前，本项目按私有、不可分发处理。
