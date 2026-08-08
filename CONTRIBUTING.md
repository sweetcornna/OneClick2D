# 参与 OneClick2D 开发

`docs/DEVELOPMENT_STANDARDS.md` 是规范来源。

## 开始前

1. 阅读 docs/index.md、项目章程、需求及相关架构/质量/隐私规范；
2. 检查 OPEN_DECISIONS.md，不把待定栈/供应商/模型/阈值变成事实；
3. 关联需求/issue；重大变化使用 ADR；
4. 仅使用权利清楚的 fixtures；
5. Gate F 前生产化工作默认不 Ready。

## 当前命令

### 产品路径（`oneclick2d/`，纯标准库）

```bash
python -m unittest discover -s tests -p "test_*.py"
python scripts/check_registry_mirrors.py
python -m oneclick2d registries --show-parameters
python -m oneclick2d generate --source /path/to/cut-out-subject.png --output out/
python -m oneclick2d verify --package out/<name>.oc2d --psd out/<name>.psd
```

产品路径**只允许标准库**：PNG/JPEG 编解码、PSD 读写、ZIP 打包和 JSON Schema 校验都在仓库内实现。这样产品路径可运行而不预判 D-005，所以不要为它引入第三方依赖；`oneclick2d/` 也不得导入 `spikes/`。改动关键不变量时，请让**第二个实现从已发布字节**验证，而不是复用生产者的中间状态：中性合成必须逐像素保留可见原作，生成覆盖必须被前层遮挡，任何姿态都不得使三角面积归零或翻转 winding。`registries/*.json` 是被 CIR 摘要引用的权威镜像，YAML 仅供评审，改任一侧都要跑 `check_registry_mirrors.py`。

测试用小画布（64–256 px）跑真实管线，是因为栅格化是纯 Python；这靠注入 `DimensionEnvelope` 实现，**不得**用它放宽任何校验，FR-001 的出厂信封另有专门用例验证。

### Gate F 预研（`spikes/`，可丢弃）

以下模型支持命令使用无隔离边界、仅限本机的原生 Linux worker。GUI 的模型模式和显式 `model` 命令应使用已抠背景、背景透明的角色图（通常为 PNG）；不透明背景会被源侧保真统计计为可见区域，而语义层通常只覆盖角色。该提示不是新的硬阻断，已抠背景也不保证通过中性保真门。

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

文档检查仅是立项 lint；固定 `smoke` 命令仍是标准库、进程内、可丢弃的合成编排 smoke。`raster.normalize.pillow.v1` 和含共享 seeded trajectory 的 `simple-cutout.comparator.pillow.v1` 另以 hash-pinned Pillow 12.1.0 运行本地非计分 ingest/comparator preflight；它们不是生产栈、也不是 Gate F 核心可行性证据。`gui` 仅在 loopback 接收权利明确的本地 PNG/JPEG，可显式运行固定区域 deterministic baseline，或在同一规范化边界后调用无隔离边界、仅限本机的原生 Linux worker 的固定 See-through V3 NF4 profile；模型路径只放行固定身份、重建图、清单内语义 RGBA/深度图和受检 PSD，全部成功前不得记录 `model_used: true`。GUI 不生成 `.oc2d`，不会自动删除本地工作区。显式 `model` 命令使用同一固定 profile；`diagnose-fidelity` 只读诊断已完成运行的中性保真漏失，不修改运行产物、不是验收门且不改变任何阈值；成功只写 `LOCAL_FIDELITY_DIAGNOSIS_COMPLETED` 且 `GATE_F_NOT_EVALUATED`，不证明模型质量、蒙版语义、隐藏区域真实性或任何 Gate F 结论。`motion` 仍按原阈值计算和记录 active v6 的中性保真门；未通过时继续生成 37 帧 bbox quad/affine `research_draft`，并在 `quality.review_items` 以 `FIDELITY_GATE_NOT_PASSED` 记录三项实测值和门限，profile 身份不匹配仍硬失败。candidate v0.3 报告没有等价自由扩展点，因此 `model-candidate` 和独立重算的 `verify-model-candidate` 仍要求中性保真通过；它们只生成或核对单项 ontology/provenance/comparator parity 技术预检，不生成评审 ballot、paired outcome、`F-USABLE` 或 20 项结果，成功只写 `LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED` 与 `GATE_F_NOT_EVALUATED`。supporting weight 许可元数据仍不完整，所以禁止再分发权重、禁止权重入库、禁止产品使用，结果仍为 `GATE_F_NOT_EVALUATED`。Gate F 前可执行预研必须放在 `spikes/`，生产模块不得导入它。技术栈 ADR 通过后，在 README、CONTRIBUTING 和 CLAUDE.md 同一变更补齐固定安装、构建、格式、lint、类型、测试和开发命令。

默认宿主中立模型 profile 为 `see-through.v3.nf4.1280.source-preserve.v6`，当前 `runtime.kind = native-linux`、`runtime.isolation = none-host-local`，固定声明为“无隔离边界、仅限本机”。v6 保留 v5 的固定 alpha 噪声底清理、最前可见语义层原图 RGB 回填、按清理后各层最大 alpha 输出中性重建与可见像素保真证据，以及一次性 challenge、源图 SHA-256 和产物清单摘要的本次运行/本次产物清单绑定；受信父进程独立重算校验。该绑定不证明被钉死的 entrypoint 确实执行过，不是密码学执行证明或可信执行环境保证；完全控制 worker 运行环境者仍可对自造产物计算自洽清单。该后处理不证明蒙版语义、补全或绑定质量，模型报告必须保持 `review_required`。没有 motion 产物时 GUI 必须显示网格、参数绑定和动态预览未生成；存在受检 motion 产物时只能显示 `research_draft`，并明确 `.oc2d`、`.moc3` 与 mesh-delta 未生成。历史 `see-through.v3.nf4.1280.wsl2.v2`、`see-through.v3.nf4.1280.wsl2.source-preserve.v3`、`see-through.v3.nf4.1280.wsl2.source-preserve.v4` 与 `see-through.v3.nf4.1280.wsl2.source-preserve.v5` 结果只能按各自原 profile/入口摘要只读验证，禁止追溯声称获得 v6 语义或 v6 的运行/产物清单绑定。

## 分支/提交/评审

- `type/issue-short-name`，短生命周期；
- imperative Conventional Commits：`feat:`、`fix:`、`docs:`、`test:`、`refactor:`、`perf:`、`build:`、`ci:`、`chore:`；
- 禁止 main 直推、force push、自合并或跳检查；
- 至少一个非作者批准；高影响领域需相应独立 owner；
- 建议 <400 逻辑行，否则说明原子性；
- AI 贡献没有较低门槛。

## 变更证据

通常包括 schema/spec/example、producer/consumer、原因码、negative/cancel/retry、provenance/seed/cache、unit/property/contract/integration/E2E、模型/数据卡、资源/成本、隐私/安全/删除/遥测、许可、可访问性、双导出/reopen/migration 和 rollback。

## 内容与 fixtures

禁止提交客户/私有/专有艺术、摄像头数据、生成客户项目/PSD、生产日志/ID、个人路径、签名 URL、凭据、权重、私有数据集或 executable/unknown 模型。每个媒体 fixture 记录来源、权利、用途、摘要、owner 和下架路径。

## PR 清单

使用 `.github/pull_request_template.md`；“不适用”需说明。任何变更不得增加 `.moc3` 创建/解析/检查/逆向、以 Live2D 为品牌或暗示 Cubism 兼容/关联。

## 安全

按 SECURITY.md 私下报告。不要在公开 issue 中放利用细节、内容、凭据或敏感运营数据。
