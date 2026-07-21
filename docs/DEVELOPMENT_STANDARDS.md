# OneClick2D 开发规范

- **状态**：规范性基线
- **范围**：应用、ML/图形、契约、基础设施、模型/数据清单、测试、文档和发布。

## STD-001 范围和声明

使用 OneClick2D 作为内部临时代号。第三方商标只用于审核过的事实边界。禁止 `.moc3` 创建/解析/检查/fixtures/逆向、暗示 Cubism 兼容或官方关系。外部声明必须映射版本化证据和 kill switch。

## STD-002 需求与决策

行为引用稳定 `FR-`、`NFR-`、`TEL-` ID，包含 owner、gate、分母和证据。禁止“效果好”“速度快”等不可测验收。架构、格式、坐标、数据/网络/摄像头、模型/数据权利、性能、兼容和外部声明的重大变化使用 ADR；已接受 ADR 只能 supersede，不能重写。

## STD-003 Git 与评审

- 保护 `main`；禁止直推、force push、跳过 hook/CI、自合并；
- 短分支 `type/issue-short-name`；
- imperative Conventional Commits；
- 至少一个非作者批准；契约、隐私安全、租户、parser/package、模型/数据/许可、阈值、导出和发布需独立领域评审；
- 建议 PR <400 逻辑行（不含生成/fixture），超过需说明原子性；
- AI 生成代码承担相同权利、测试和评审责任。

## STD-004 契约优先

使用 OpenAPI 3.1、JSON Schema Draft 2020-12 和机器可读注册表。先改权威 schema/spec/example，再生成类型、加正负/版本/迁移 fixtures，再改 producer/consumer。禁止手写重复 DTO 成为第二来源。

所有 JSON 严格拒绝 duplicate key、NaN/Inf、非法 UTF-8、过深/大结构和声明范围外数字。canonical JSON 使用 RFC 8785 + I-JSON；超 `2^53-1` 整数为固定宽度字符串。

## STD-005 坐标与媒体

左上原点、X 右/Y 下、UV 左上 `[0,1]`、角色解剖学左右、screen direction 显式；持久 sRGB、linear mask、straight alpha；角度 degree、时间 second；每个含糊字段声明 unit/space。转换和 sign 逻辑集中实现，以非对称 fixtures 验证。

## STD-006 阶段契约

每个阶段：不可变输入、attempt/contract identity、稳定种子、输出影响摘要、原子验证发布、取消/进度、CPU/GPU/RAM/磁盘/时间/输出上限、类型化失败/回退、scratch 清理和出口规则。晚到/重复工作不能覆盖发布结果。

## STD-007 确定性和缓存

所有随机操作显式 seed；子 seed 由稳定 hash 派生；遍历排序。缓存键包含 code/config/model/template/schema/precision/runtime/execution profile 等所有影响输出的摘要。mutable alias 只能在运行开始前解析为不可变身份。

## STD-008 安全编码

不可信数据在分配/使用前验证，模型 tensor 推理后立即验证，artifact commit/cache read 时验证，CIR 在 render/export 前验证，包 extraction 前验证路径/摘要/预算。禁止 eval、unsafe deserialize/pickle、用户控制 shell/dynamic import/path/URL、catch-all 吞错、raw exception 给用户和内容日志。

## STD-009 测试

要求 unit/property、schema/contract、fake-transport integration、视觉 golden、冻结模型 benchmark、browser/editor E2E、failure injection、package/parser fuzz、accessibility、camera network/storage、deletion/restore 和双租户负例。硬边界测试不得 release quarantine；escaped defect 必须有最小永久回归。

## STD-010 fixtures 与权利

Git/CI/docs/screenshots 只用 purpose-created、public-domain、明确可再分发或权利已记录的合成资产。记录 asset-level rights ledger。禁止客户内容、摄像头数据、生产日志、私有路径、专有角色素材、未批准模型权重/数据和 mutable remote asset。

## STD-011 ML/数据

模型和数据拥有不可变身份、代码/权重/数据许可、用途、group-disjoint split、card、切片/校准、资源证据、限制、owner、复核、rollback/replacement/takedown。权重、pre/postprocess、threshold、resolution、provider 或 precision 变化创建新版本并比较 prior。

## STD-012 隐私与可观测性

内容和内容衍生元数据最小化；生产日志 schema allowlist，禁止 collect-then-redact、body、session replay、camera payload、art hash/name/path/URL/free text。认证艺术响应 no-store；浏览器受控持久存储不保存内容。新字段/store/provider/region/purpose/recipient/retention 必须先分类和评审。

## STD-013 性能与成本

重阶段变化在代表 profile 报告 wall p50/p95、RAM/VRAM/disk、每 start 和 blended success 成本。Renderer 报告 frame p50/p95/p99、long/drop、draw calls、textures/bytes、triangles/masks。OOM 只允许一次声明的 downshift。

## STD-014 CI/发布

PR CI：format、generated drift、schema/registry、type/lint、unit/property、affected integration/golden/fake E2E、dependency/license/SBOM/secret/package security。Nightly/release 增加 GPU/model/browser/editor/perf/fuzz/soak。失败不能自动 rerun 成 pass。

发布来自 clean tagged checkout、immutable dependency/model、SBOM/provenance/checksum；canary、rollback、support matrix、limitations 和声明证据齐备。

## STD-015 可访问性

在画布交互前设计键盘/语义等价控制。visible focus、non-color status、reduced motion、200% zoom、clear progress/cancel/retry；摄像头有完整手动替代。

## STD-016 Definition of Ready

包含：需求/决策 ID、owner/reviewer、问题/范围/非目标、acceptance/negative/cancel/retry、依赖和权利、隐私安全可访问性、fixtures/slices、遥测决定、估算/外部 lead time、compatibility/migration、rollout/rollback。Spike 需 timebox、问题、证据、处置标准。

## STD-017 Definition of Done

验收及负路径通过；契约/迁移/文档/runbook 同步；idempotency/recovery/deletion、可访问性、资源/成本、模型/数据/许可、双 export/reopen、rollback/disablement 和产品接受有证据。代码写完不是 Done；无 owner TODO/过期例外。

## STD-018 当前校验声明

`python scripts/validate_docs.py` 结果只能称“立项文档 lint 通过”，不能称 contracts valid、format conformant、secure 或 feasible。正式一致性使用固定标准工具、语义验证、真实包/渲染和 tamper negatives。
