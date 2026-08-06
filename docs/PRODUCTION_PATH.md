# 产品路径实现说明

- **状态**：已实现，未经 Gate 验收
- **范围**：`oneclick2d/` 包的实现映射、能力边界与验证方式
- **责任人**：Gate 0 任命

本文说明 `oneclick2d/` 实现了什么、明确没有实现什么，以及每条声明的证据来源。实现存在**不等于** Gate F 通过或产品可用；权威顺序与硬边界见 [docs/index.md](index.md) 与 [CLAUDE.md](../CLAUDE.md)。

## 1. 阶段映射

[系统架构](ARCHITECTURE.md) §7 的阶段 DAG 逐项对应实现模块：

| 阶段 | 模块 | 生产者类型 |
|---|---|---|
| `INGEST_SCAN_NORMALIZE` | `stages/intake.py` | deterministic |
| `VALIDATE` | `stages/suitability.py` | deterministic |
| `DECOMPOSE` | `stages/decompose.py` | deterministic（可替换为 model-backed 提议者） |
| `PLAN_AND_BOUNDED_COMPLETE` | `stages/synthesize.py` | deterministic（可替换为 model-backed 补全器） |
| `SYNTHESIZE_LAYERS` | `stages/synthesize.py` | deterministic |
| `MESH_AND_MINIMAL_RIG` | `stages/rig.py` | deterministic |
| `VERIFY_PROJECT` | `validation.py` | deterministic |
| `COMPILE_PREVIEW` | `render.py` | deterministic |
| `EXPORT_OC2D` | `export/oc2d.py` | deterministic |
| `EXPORT_PSD` | `export/psd.py` | deterministic |
| `VERIFY_EXPORTS` | `export/release.py` | deterministic |

阶段框架（身份、seed、资源上限、取消、typed outcome、attempt 所有权）在 `pipeline/`；端到端编排在 `generate.py`；命令行入口在 `__main__.py`。

## 2. 只用标准库

产品路径不依赖任何第三方包。PNG/JPEG 编解码、PSD 读写、ZIP 打包与 JSON Schema draft 2020-12 校验都在仓库内实现。

这是刻意选择：[D-005](OPEN_DECISIONS.md)（语言/框架/工具链）仍为 Open，纯标准库让产品路径可运行而**不预判**该决策。代价是栅格化为纯 Python，1024×1024 单次运行需要数分钟；这属于已接受的权衡，不是性能声明。Pillow 只作为**独立 oracle** 用于交叉验证，不是运行依赖。

JSON Schema 校验器对**未实现的关键字直接拒绝编译**，而不是忽略：被忽略的关键字会把真实约束变成橡皮图章。仓库现有 39 个 schema 全部可编译，因此可以确认没有约束被静默跳过。

## 3. 能力边界

以下每条都是实现的真实限制，不得在对外材料中省略：

- **语义拆层来自确定性解剖学布局先验**，按实测 subject 包围盒推算五官与躯干位置。布局先验只定位区域、不识别解剖结构，因此本体完备性一律报 `LOW_CONFIDENCE`，并附 `ONTOLOGY_SLOT_LOW_CONFIDENCE`。报 `PRESENT` 会高估证据。
- **有限补全是边缘延展**（最近已知样本的广度优先扩散），不是生成模型，也**不恢复真实隐藏内容**。产出是有界、可复现的既有像素延续。
- **没有校准数据集**，因此全部 `confidence_facts.score` 与 `threshold_band` 为 `unavailable`。给出数字会把未校准猜测伪装成校准分数。
- **model-backed provenance 在缺少不可变 model ID、weights SHA-256 与权利登记记录前硬失败**，`build_project` 直接拒绝。
- **输入信封与阈值是章程暂定值**（FR-001 的 1,024–8,192 px、≤40 MP、≤25 MiB），改动需 Gate 决策。
- **PSD 互操作证据仅限"与写入器无共享代码的第三方读取器可打开"**。[PSD 导出配置](PSD_EXPORT_PROFILE.md) §7 要求的 Photoshop/Krita 精确版本矩阵与许可证据仍是 Gate 决策，尚未取得。
- 报告状态因 `LOW_CONFIDENCE` 槽位止于 `pass_with_review`，绝不会是无保留的 `pass`。

风险登记见 [R-022](RISK_REGISTER.md)。

## 4. 关键不变量与验证方式

三条不变量由**独立第二实现从已发布字节**验证，而不是复用生产者中间状态。这不是形式主义：三个真实缺陷都是靠这种方式才发现的。

1. **原作像素保护**。正确命题是"中性合成结果逐像素保留可见原图"（[CIR 规范](CIR_SPEC.md) §11.4），而非"生成蒙版避开源可见坐标"。后者必然误报：被前层遮挡的像素在源图有内容、在合成中不可见，对其生成正是运动要揭示的部分。`validation.py` 按 draw order 从已发布图层纹理重建合成再与源图对比，实测偏差为 0 像素。
2. **生成区域必须被遮挡**。有限补全只填"draw_order 更大的层的可见并集"覆盖的区域；羽化也须再与该区域求交，否则渐变会溢出到可见原作之上。
3. **网格在任何姿态都不得退化**。闭眼/闭嘴 delta 按顶点到中线的距离缩放并限幅在 1.0 以下；完全塌缩会使三角面积归零并翻转 winding，被 FR-010 判为 blocking。

验证覆盖中性、逐参数极值、固定组合与带种子轨迹共 35 个姿态。

## 5. 非循环摘要域与双输出

[包一致性规范](PACKAGE_CONFORMANCE.md) §3 的摘要域按原样实现：`manifest.json` 是权威项目内容且不含报告摘要；`validation.json` 与 `run-manifest.json` 绑定 project payload 摘要；`package-index.json` 记录除自身外每个成员；最终归档摘要只存在于归档外的 release record。

发布是原子的：两个产物必须由**独立读取器**重新打开、重算摘要、重跑语义验证，并绑定同一 `project_payload_sha256`。PSD 失败**阻断发布**，不会静默降级为仅 `.oc2d`（[PSD 导出配置](PSD_EXPORT_PROFILE.md) §7）。归档使用固定成员顺序与固定时间戳，因此同一 revision 可复现为逐字节相同的归档。

## 6. 测试

固定命令见 [CONTRIBUTING.md](../CONTRIBUTING.md)。测试用 64–256 px 小画布跑**真实**管线（栅格化是纯 Python，全尺寸会让固定命令不可用），这靠注入 `DimensionEnvelope` 实现，不放宽任何校验；FR-001 出厂信封另有专门用例验证。

负向用例覆盖：严格 JSON 拒绝项、PNG 五种 filter 与炸弹/CRC/交错拒绝、几何 ABI 越界与退化、路径穿越与大小写碰撞、报告绑定旧 payload、篡改产物、blocking 状态发布尝试、PSD/CIR 合成不一致与 PSB。
