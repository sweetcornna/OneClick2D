# 2026-08-02 N 系列修复轮 · 主模型验收记录

> 基线 `ee953de`;全部改动仍在工作树**未提交**。对应交接文档 §9→§10。
> 仅针对 `spikes/` 可丢弃预研;`GATE_F_NOT_EVALUATED` 不变。

## 执行链路

1. **分包修复**(Codex 写模式,两包文件集互不相交并行):
   - 包 A(`task-msbedj81-z594tf`):N1 attestation 归真 + N3 PNG 硬化 + N4 阈值统一。
   - 包 B(`task-msbedzjh-jgbf50`):N2 描述符先验精确核对 + N8 共享模块抽取。
   - N5(`.gitattributes`)由主模型直接落实。
2. **双路对抗复核**(只读):`codex-adversarial-review.md`(session 019fc14a…)、`opus5-review.md`。
3. **收尾修复**(Codex,`task-msbhwa1q-90ydqr`):R1–R8,消化两路复核发现。
4. **主模型独立复验**:每阶段自跑完整套件 + 越界检查 + 关键实现抽验。

## 复核判定

| 项 | 判定 | 验证强度 |
|---|---|---|
| N1 attestation 入报告 + 算法标识归真 | CONFIRMED | 13 组变异注入全 fail-closed;HEAD 对比 261 例差分模糊,0 分歧 |
| N2 描述符先验核对 | CONFIRMED | `builtins.open` + `Path.open` 双探针实证:bundle 目录内仅 index 被打开,80 个产物零读取 |
| N3 `_png_facts` 硬化 | CONFIRMED | 5 个调用点全传期望画布;探针实证 `load()` 未被调用;无漏改的裸 PIL import |
| N4 阈值改读 profile | CONFIRMED | v4 恒为 31(worker 硬钉),v2/v3 恒为 15 且 identity 与 HEAD 逐字段一致 |
| N5 `.gitattributes` | CONFIRMED | 对照上轮 28 项 digest 清单逐条核对,raw 绑定全覆盖,canonical-JSON 绑定正确豁免 |
| N8 共享模块 | CONFIRMED | HEAD vs 工作树 24 组字节级等价实跑,全 OK |

## 复核抓出的第二批缺陷(已全部收尾修复)

- **D1 / Codex#2 [中]** 报告契约变更未升版,历史持久化报告(含合法 v2/v3)复验硬失败且文案误导为篡改 → `format_version` 升 0.4.0,loader 版本感知:0.3.0 仅对历史 v2/v3 放行(按投影严格比对),v4 的 0.3.0 与未知版本给专门错误;附**静态字节 fixture**(非用当前 builder 生成)回归。
- **Codex#3 [低]** attestation 组件设备只要求任意 `cuda*`,`cuda:1` 可与顶层 `cuda:0` 并存 → 收紧为精确等于顶层 `execution_device`,覆盖裸 `cuda`/`cuda:1`/混合设备负例。
- D2 reason_codes 列表别名 → 两处各自复制;D3 `MAX_IMAGE_PIXELS` 无锁交错 → 三处共用带进程级 `threading.Lock` 的上下文管理器(`raster.py:70`)+ 线程交错回归;D4 死参数 → 调用点传入;D5 危险默认参数 → 改必填 keyword-only;D6 纵深预算测试改名标注;D7 交接文档措辞同步(§2.2 F2 + 新增 §10);D8 `non-active` 文案。

## 显式不修(留决策,均属 digest 链决策域,建议一并定夺)

- **Codex#1 [中] attestation 强绑定**:当前 `entrypoint_attestation` 通过校验后仅剩 3 个 device 列表 + 1 个 bool 是自由量,其余全是编译期常量——它是"worker 声称固定 device policy 成立"的**可逐字复现自述**,写得动 `model-result.json` 的人可照抄一份合法摘要。不同于 `_source_trust` 的像素级绑定。**不得描述为执行期密码学证明。** 修复需把 attestation 绑到每次运行的挑战/source SHA/产物清单摘要,这要改 digest 绑定的入口脚本并连带更新 profile digest。
- **N6 profile_id 升版**:profile 字节与入口语义已变但 ID 仍是 `…source-preserve.v4`;涉及 CLAUDE.md 固定 profile 名,且历史 v2/v3 profile 内容从未入库(`LEGACY_*_PROFILE_SHA256` 不可验证)。
- **N7** 按复核结论接受现状(GUI 单帧全量验证 ~30 ms/帧,loopback-only)。

## 最终验收实测(主模型宿主机,macOS + Python 3.14.6 + Pillow 12.1.0 venv)

- 完整套件:**Ran 225, OK (skipped=16)** —— 含 Codex 沙箱因禁 socket bind 跑不了的 12 项 GUI 测试。
- `preflight`:`LOCAL_TECHNICAL_PREFLIGHT_PASS` + `GATE_F_NOT_EVALUATED`。
- 文档 lint:通过(38 Markdown / 39 JSON,含本轮文档改动后重跑)。
- 越界检查:`git status` 改动文件全部落在各包合同允许范围内。
- macOS 必须设 `TMPDIR` 指向已解析路径(`/var`→`/private/var` 符号链接会被工作区硬化校验拒绝)。

## 遗留

P1-2(Windows + WSL2 GPU 真机链路)仍开放,macOS 无法执行。
