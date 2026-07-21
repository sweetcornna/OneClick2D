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
```

结果仅是立项文档 lint。技术栈 ADR 通过后，在 README、CONTRIBUTING 和 CLAUDE.md 同一变更补齐固定安装、构建、格式、lint、类型、测试和开发命令。

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
