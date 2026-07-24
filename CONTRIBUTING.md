# 参与 OneClick2D 开发

`docs/DEVELOPMENT_STANDARDS.md` 是规范来源。

## 开始前

1. 阅读 docs/index.md、项目章程、需求及相关架构/质量/隐私规范；
2. 检查 OPEN_DECISIONS.md，不把待定栈/供应商/模型/阈值变成事实；
3. 关联需求/issue；重大变化使用 ADR；
4. 仅使用权利清楚的 fixtures；
5. Gate F 前生产化工作默认不 Ready。

## 当前命令

```bash
python scripts/validate_docs.py
python -m unittest discover -s tests -p "test_*.py"
python -m spikes.gate_f_runner smoke --run-id run.local-smoke
python -m spikes.gate_f_runner preflight --run-id run.local-technical
python -m spikes.gate_f_runner gui
python -m spikes.gate_f_runner model --source "C:/path/to/right-cleared.png" --run-id run.local-model
python -m spikes.gate_f_runner motion --run-id run.local-model
```

文档检查仅是立项 lint；固定 `smoke` 命令仍是标准库、进程内、可丢弃的合成编排 smoke。`raster.normalize.pillow.v1` 和含共享 seeded trajectory 的 `simple-cutout.comparator.pillow.v1` 另以 hash-pinned Pillow 12.1.0 运行本地非计分 ingest/comparator preflight；它们不是生产栈、也不是 Gate F 核心可行性证据。`gui` 仅在 loopback 接收权利明确的本地 PNG/JPEG，可显式运行固定区域 deterministic baseline，或在同一规范化边界后调用隔离 WSL2 worker 的固定 See-through V3 NF4 profile；模型路径只放行固定身份、重建图、清单内语义 RGBA/深度图和受检 PSD，全部成功前不得记录 `model_used: true`。GUI 不生成 `.oc2d`，不会自动删除本地工作区。显式 `model` 命令使用同一固定 profile；`motion` 只给通过中性保真校验的 active v4 运行附加 37 帧 bbox quad/affine 研究初稿，GUI 可播放但不得称专业绑定或 Live2D 成品。supporting weight 许可元数据仍不完整，所以禁止再分发权重、禁止产品使用，结果仍为 `GATE_F_NOT_EVALUATED`。Gate F 前可执行预研必须放在 `spikes/`，生产模块不得导入它。技术栈 ADR 通过后，在 README、CONTRIBUTING 和 CLAUDE.md 同一变更补齐固定安装、构建、格式、lint、类型、测试和开发命令。

默认 `source-preserve.v4` 模型 profile 先以固定 alpha 噪声底清理各语义层，再在最前可见语义层回填原图 RGB，并以清理后各层最大 alpha 输出中性重建及可见像素保真证据；该后处理不证明蒙版语义、补全或绑定质量。模型报告必须保持 `review_required`。没有 motion 产物时 GUI 必须显示网格、参数绑定和动态预览未生成；存在受检 motion 产物时只能显示 `research_draft`，并明确 `.oc2d`、`.moc3` 与 mesh-delta 未生成。历史 `v2` 与 `source-preserve.v3` 结果只能按各自原 profile 摘要验证，禁止把新后处理能力追溯写入旧结果。

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
