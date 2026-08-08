# Gate F 预研审计修复 —— 任务交接

> 交接时间：2026-08-01（UTC）。适用分支：`feat/gate-f-runner`。
> 本文只描述 `spikes/` 下的**可丢弃预研**修复工作。它不是 Gate F 结论、不是 schema/package conformance 证据、不是模型质量或 PSD 互操作证明。所有本地运行状态仍为 `GATE_F_NOT_EVALUATED`。

## 0. 一句话现状

此前的 Gate F 预研安全/契约审计、分包修复与对抗复核历史见 §2–§12；完整套件与确认性复核后来均已补跑。当前 active profile 已迁移为宿主中立的 v6，并在原生 Linux 真机上完成 `model` 步；`motion` 被中性保真门拒绝，因此 P1-2 仅部分推进、仍未收口，详见 §13。全部状态仍为 `GATE_F_NOT_EVALUATED`。

## 1. 这个任务是什么

上上一轮会话对 Gate F 未完成实现跑了一次**三路并行只读审计**（维度：runtime/v4/motion 正确性、model-candidate 独立校验器、仓库契约一致性），产出 23 条原始发现（去重后 19 条），但综合与修复阶段被中止。

上一轮会话（WSL Claude Code，session `af2a0fa9-287b-4910-9550-30f16eef88af`）恢复该任务，作为主模型做决策与验收，把修复工作按**互不相交的文件集**切包，派给 Codex 执行，每包完成后由主模型**独立复跑验证**，再用对抗复核找修复本身引入的新缺陷。

原始审计记录（唯一存留副本）：`.claude/workflow-runs/whtdulpa9/journal.jsonl`（3 条记录 = 3 个审计维度，逐条含 severity / file / line / issue / evidence / fix / test / confidence）。该目录**未跟踪**，会话临时目录里的 `audit_findings.json` 已随 `/tmp` 清理丢失——**接手前请先备份这个 journal**。

## 2. 已完成的工作

### 2.1 轮次时间线（UTC，2026-08-01）

| 时间 | 动作 | 结果 |
|---|---|---|
| 09:46 | 恢复上下文，确认修复前基线 | 178 项测试全绿、文档 lint 通过——**所有缺陷都是“测试测不到”的校验漏洞，不是既有测试失败** |
| 09:51 | 第一阶段三包并行派单（A 契约 / B worker+v4 / C workbench+motion） | — |
| 10:19–10:31 | 三包逐包验收 | A ✅ 5/5，B ✅ 4/4，C ✅ 6.5/7 |
| 10:32–10:36 | C7 收尾（`__main__.py` 当初被排除在 C 包外） | ✅ |
| 10:32–12:09 | 第二阶段：`model_candidate.py` 校验器独立性（5 条，依赖 motion 侧新增共享重算接口，必须串行） | ✅ |
| 12:12–12:45 | 第一轮对抗复核（working-tree scope） | 5 条确认发现 |
| 12:47–13:23 | 第四轮三包修复（acceptance 信任边界 / workbench 参照可信化 / candidate 阈值对齐） | ✅ 逐包复验 |
| 13:27–14:30 | fixture 集成修复：workbench 硬化后 motion/candidate 测试 fixture 仍用旧方式伪造运行 | ✅（生产代码未动、断言未弱化） |
| 13:24–13:52 | 第二轮（聚焦）对抗复核 | 5 条中 4 条确认闭环，1 条未彻底闭环 + 2 条修复引入的新缺陷 |
| 14:31–15:04 | 终轮三包修复（F1 CLI 受信源 / F2 bundle 资源预算 / F3 profile digest 归真） | ✅ 逐包复验 |
| 15:04 | 派出**最终确认性复核** + 后台完整 Pillow 套件 | ❌ **结果丢失** |
| 15:25 | 会话进程重启，两个后台任务被标记 stopped，会话中断 | — |

### 2.2 闭环发现清单

**第一阶段 · A 契约包**（`acceptance.py` / `candidate_baseline.py` / comparator schema）

1. [高] bundle 校验不把帧字节绑定到报告描述符——伪造 `candidate-frame-000.png` 并同步更新索引摘要，仍能拿到 `LOCAL_TECHNICAL_PREFLIGHT_PASS`。→ 按 arm/index/name/digest/length 绑定并解码校验 PNG（canvas/RGBA/sRGB/renderer profile）。
2. [高] paired-outcome 用 `bool()` / `str()` 强转——JSON 字符串 `"false"` 被算作 True，可凑出 `f_usable_count: 20`。→ 严格字段集 + `type(x) is bool`。
3. [高] comparator v0.3 schema 相比 v0.2 反而**放松**了约束（sequence/input 退化为无约束对象、frames 无 item schema）。→ 恢复 v0.2 全部约束，只改 renderer profile 与 premultiplied 事实。
4. [中] candidate config 读取先查冻结 digest 后查 `format_version`，历史 v0.1 文档得到的是通用“不匹配冻结基线”而非明确的“版本不支持”。→ 版本判定前置。
5. [低] `test_example_is_the_only_accepted_v0_2_config` 测试名过期（producer 已到 v0.3）。→ 改名并补 v0.2/v0.1 拒绝用例。

**第一阶段 · B worker/v4 包**（`model_worker.py` / `see_through_v3_nf4_source_preserve_v4.py`）

6. [高] NF4 Marigold 路径无视 `--cpu_offload`，直接把 VAE/UNet/text encoder 搬上 CUDA，而 profile 却记录 `cpu_offload: true`。→ 新增仓库自持的 `nf4_marigold_device_policy.py`（digest 绑定），拦截上游 CUDA 迁移，**实测记录组件设备**而非按 CLI flag 推断。
7. [中] 源 RGB 回填不检查源 alpha——透明源像素的未定义 RGB 被变成可见输出。→ 限定 `source alpha > 31`。
8. [中] PSD 组装调用上游 `further_extr`，会把源 `fullpage` RGB 无条件写进 nose/mouth 图层，**撤销 v4 的隐藏像素保留**，而校验只看 PSD 结构不看像素。→ 从后处理语义层重建并独立回读校验，绑定 `psd-postcorrect.v1` 算法标识。
9. [中] 新增的 model/worker/workbench/motion 测试未加 Pillow 守卫，破坏“纯标准库可跑”的固定命令。→ 补 `skipUnless` 守卫。

**第一阶段 · C workbench/motion 包**（`model_workbench.py` / `model_motion_draft.py`）

10. [高] 中性保真度以**重建可见像素**为分母——1280×1280 不透明源配一个匹配像素的重建也能 `status: pass`，并据此解锁 motion 与 model-candidate。→ 改为源 alpha 分母 + 覆盖率硬性要求（新增 8 个 additive 字段，`status` 语义不变）。
11. [中] RGB “精确”匹配率先 `difference.convert("L")` 再数零，通道差被舍入掉——`(10,20,30)` vs `(10,20,31)` 报 ratio 1.0。→ 三通道 OR/max 合并，workbench 与 motion 两处同修。
12. [中] 中性帧走 `_clean_neutral_image(reconstruction)` 而非 `_render`，中性校验自证其说，且与浏览器渲染器不一致。→ 中性帧走真实 `_render` 管线。
13. [中] 共享 subject matte 用 `MinFilter(5)` 连内部透明孔洞一起腐蚀，违反“只削外轮廓”契约。→ 改为外轮廓 flood-fill 限定。
14. [低] `model_motion_draft.py` 整文件 CRLF churn（普通 numstat 1062/1039，忽略行尾后仅 58/35）。→ 规范化，两口径一致。
15. [中] README/CONTRIBUTING/CLAUDE.md 当时未说明模型命令的宿主 shell，`C:/...` 路径在 WSL 下按相对路径解析并以通用错误失败。→ 三处文档补说明；**C7 收尾**：POSIX 下 `C:/...`、`C:\...` 在工作区创建前即拒绝，退出码 64，有界拒绝码 `WINDOWS_SOURCE_PATH_REQUIRES_WINDOWS_HOST_SHELL`，不回显路径。该 Windows/WSL2 限制描述的是历史 v4/v5 路径，已由 §13 的宿主中立 v6 原生 Linux 运行时取代。

**第二阶段 · model_candidate 校验器包**（`model_candidate.py` / schema）

16. [高] 校验器比较不区分类型（`False == 0`、`0 == 0.0`），claim 与数值类型可被篡改而通过。→ 字节级精确报告比对。
17. [高] 候选帧与 motion lineage 直接取自自述的上游 motion 产物，未独立重算——协调一致的伪造可全线通过。→ 37 帧独立重算 + lineage 从重算记录重建。
18. [中] deterministic-underpaint provenance mask 由 alpha 增量推导，漏掉 alpha 不变但 RGB 被改的生成像素。→ underpaint 操作掩码化。
19. [中] 被标为 `source-visible` 的 mask 其实是并集，把源透明的生成/暴露像素也算成 source-visible，两个 provenance 分类互相矛盾。→ 分区不变量（source-visible 与 source-transparent-exposed 互斥，并集等于语义并集）。
20. [中] model-candidate report schema 把 profile/input/provenance/sequence/psd/validation 全声明成无约束对象，不是权威契约。→ 收紧到权威契约。

**第四轮（第一次对抗复核的 5 条）**

21. [高] `verify_bundle` 仍信任 producer 产出的报告。→ 改为**独立重算**确定性 purpose-created 报告与帧并逐字节比对；统计类型精确比较；comparator schema 闭合 patches/rendering。
22. [高] workbench 保真度的“参照”本身来自模型进程产出。→ 从受信 normalized input **独立重建** 1280×1280 规范源画布，与模型进程产出的 `src_img.png` 逐像素比对后才计算保真度；同时修正本身有缺陷的测试 fixture。
23–25. [中] candidate 侧 source 可见性阈值与 v4 不一致、winner RGB 比较范围过宽、`activation_blockers` 非封闭枚举。→ 阈值统一为 31（config/schema/README 同步记录 `visible_alpha_threshold: 31`，冻结 config hash 连带更新并同步到 report schema 内嵌常量）；winner RGB 比较限定在 winner ∩ source-visible；`activation_blockers` 收紧为恰好五码的封闭枚举；alpha 0/31/32 三点集成测试覆盖分类边界。

**终轮（第二次对抗复核的 3 条新缺陷）**

- **F1** [高] `model` CLI 无法产出可激活运行：workbench 信任硬化后校验要求 `trusted-model-source.png`，但 CLI 路径从不生成它——文档化的 `model → motion → model-candidate` 工作流必然在第一步后中断。→ CLI 复用 upload 路径的受信源生成逻辑，在 worker 调用前发布该文件，补端到端命令链回归。
  （本次交接已独立确认：`run_normalized_model_workbench()` 在调用 worker 前 `_publish_bytes(trusted_source_path, ...)`，GUI 与 CLI 共用该函数——见 `spikes/gate_f_runner/model_workbench.py:1309`。）
- **F2** [中] acceptance 验证有 37.5 GiB 内存耗尽面：重算比对前把全部产物读进内存，74 个 PNG 每个可声明 512 MiB 且无总量上限，违反仓库“资源超限严格拒绝”契约。→ 先校验声明长度总量预算，再逐产物顺序比对，字节相等后才解码。（2026-08-02 复核更正措辞：实现是**产物粒度顺序处理**——单产物整体读入、受预算约束，并非块级流式/峰值恒定；同日 N2 修复又将每个描述符与可信重算证据的 (sha256, byte_length) 在任何产物 I/O 之前精确核对。）
- **F3** [中] profile 声明过期 entrypoint digest：v4 入口文件被改后，worker 用双常量同时接受新旧 digest，报告发布的仍是旧 digest，provenance 失真。→ 当轮先让 profile 记录实际执行文件的 digest、纳入 device-policy 文件并移除双 digest 例外；当时暂时保持 `source-preserve.v4`（后续 profile identity 决策已在 §11 升为 v5，原 v4 profile/入口按原摘要归档）。
  （本次交接已独立确认：当轮 profile 中 `entrypoint.sha256` 与 `entrypoint.device_policy.sha256` 与磁盘文件实算 sha256 完全一致，且各只有一个值；后续曾活跃的 v5 摘要链见 §11，当前 active v6 见 §13。）

## 3. 代码现状

分支 `feat/gate-f-runner`，最后一个提交是 `a9be957 feat: add Live2D model workbench and motion preview`。**上述全部修复都在工作树里，未提交**（每个 Codex 任务都被显式要求 `do NOT git commit`）。

- 已跟踪修改：33 个文件，`+4440 / -448`
- 新增未跟踪源码/契约：
  - `spikes/gate_f_runner/model_candidate.py`（47 KB）
  - `spikes/gate_f_runner/model_entrypoints/nf4_marigold_device_policy.py`（7.9 KB）
  - `tests/test_gate_f_model_candidate.py`（46 KB）
  - `schemas/gate-f-model-candidate/v0.1/{config,report,preflight-report}.schema.json`
  - `schemas/gate-f-candidate-baseline/v0.2/{config,report}.schema.json`
  - `schemas/gate-f-simple-cutout-comparator/v0.3/{config,report}.schema.json`
  - `examples/gate-f-model-candidate/{README.md,config.json}`

### 本次交接时实测的验收状态（Windows 锁定环境，Python 3.14.4 + Pillow 12.1.0）

| 门 | 命令 | 结果 |
|---|---|---|
| 文档 lint | `python scripts/validate_docs.py` | ✅ 通过（38 Markdown / 39 JSON，已含本文） |
| 标准库 smoke | `python -m spikes.gate_f_runner smoke --run-id run.handoff-smoke` | ✅ `status=succeeded` |
| 本地技术预检 | `python -m spikes.gate_f_runner preflight --run-id run.handoff-preflight` | ✅ `LOCAL_TECHNICAL_PREFLIGHT_PASS` + `GATE_F_NOT_EVALUATED` |
| 完整测试套件 | `python -m unittest discover -s tests -p "test_*.py"` | 见下方 §4 P0-1 |

上一轮会话在终轮修复前后已实测通过的：纯标准库全套（Pillow 相关跳过）、文档 lint、smoke、preflight，以及第四轮之前的一次完整 Pillow 套件 **200 项全绿**（修复前 178 项，新增 22 项负向/伪造/平台回归测试）。

## 4. 待接手任务

### P0-1 · 重跑完整 Pillow 测试套件

上一轮会话最后一次完整套件的结果随进程重启丢失，**必须重跑**。

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

验收标准：全绿；只允许平台性跳过（上一轮在 Linux + Pillow venv 下为 200 项通过、15 项跳过；Windows 锁定环境的跳过项集合不同，属正常）。若出现失败，先判断是**生产代码缺陷**还是**测试 fixture 仍以旧方式伪造运行**——第四轮就出现过后者（workbench 信任硬化拒绝了旧 fixture，硬化本身是对的）。**任何情况下都不要弱化断言来让测试变绿。**

### P0-2 · 重跑最终确认性对抗复核

上一轮在 15:04 (UTC) 派出的最终确认性复核结果同样丢失，且 Codex 作业注册表已清空（`codex-companion.mjs status --all` 现返回 `No jobs recorded yet`），**无法找回，必须重跑**。

复核范围：逐项确认 **F1 / F2 / F3** 三条是否真正闭环（要求具体证据，不接受“看起来对了”），并搜查这三次修复本身引入的新缺陷。前两轮复核都在“确认闭环”的同时抓出了新缺陷，这一轮同样不要预设它会全绿。

### P1-1 · 确认 F3 的下游涟漪

F3 派单时带了涟漪探针要求（若 workbench/motion/candidate 内嵌了 profile digest 期望，只报告不越界修改）。终轮各包是逐包复验通过的，但**涟漪报告的最终结论没有被汇总记录**。请在 P0-1 全绿后确认没有遗留的内嵌旧 digest 期望。

### P1-2 · 端到端真机链路验证（部分推进、仍未收口）

F1 与后续 v6 修复已经推进到真实 GPU 链路：active v6 的 `model` 步在原生 Ubuntu + RTX 5070 Ti 12GB 上返回 `LOCAL_MODEL_SPIKE_COMPLETED` + `GATE_F_NOT_EVALUATED`，CLI 没有误拒原生 Linux。`motion` 步随后被中性保真门按设计拒绝，状态为 `review_required`，因此后两步尚未执行，P1-2 全链路继续开放；量化证据与未决问题见 §13。

```bash
python -m spikes.gate_f_runner model --source "/path/to/right-cleared.png" --run-id run.local-model
python -m spikes.gate_f_runner motion --run-id run.local-model
python -m spikes.gate_f_runner model-candidate --run-id run.local-model
python -m spikes.gate_f_runner verify-model-candidate --run-id run.local-model
```

当前结果不是全链路验收通过：`model` 命令成功不等于中性保真通过，`motion` 及其下游仍被阻断。后续只能使用权利明确且**不得入库**的样本继续验证；任何命令的成功状态均须附 `GATE_F_NOT_EVALUATED`。

### P1-3 · 提交与 PR 分包

4400+ 行改动堆在一个未提交工作树里。建议按本文 §2.2 的包边界切成若干提交（契约 / worker+v4 / workbench+motion / model-candidate / 对抗复核修复），而不是一个巨型提交。注意 `.gitattributes` / 行尾：`git diff` 会对多数文件报 “LF will be replaced by CRLF”，提交前确认不会引入新的整文件 CRLF churn（发现 14 就是这个坑）。

### P2-1 · 清理杂散未跟踪文件

仓库根目录有四个杂散文件，**均非本次修复产物**，确认无用后删除；`converted.png` 若是本地测试素材请注意别误提交（CLAUDE.md 禁止用户艺术资产入库）：

| 文件 | 大小 | 时间 | 说明 |
|---|---|---|---|
| `-s` | 1 MB | 07-23 | 疑似 7 月 23 日会话的误产物 |
| `xaa` | 0 B | 07-23 | 同上（`split` 残留） |
| `converted.png` | 380 KB | 07-23 | 500×500 图片，来源不明 |
| `NUL` | 63 B | 08-01 | 内容是一行 WSL socket 错误；某处 `2>NUL` 重定向在 POSIX shell 下落成了真实文件 |

### P2-2 · 归档审计原始记录

`.claude/` 未跟踪，其中 `workflow-runs/whtdulpa9/journal.jsonl` 是三路审计 23 条原始发现的**唯一存留副本**。要么归档到仓库外的可靠位置，要么决定是否随修复一起入库（注意 `.claude/worktrees/` 下还有整份仓库副本，不要连带提交）。

## 5. 环境与固定命令

- 当前 active profile 为宿主中立的 `see-through.v3.nf4.1280.source-preserve.v6`；worker 使用 `runtime.kind: native-linux`、`runtime.isolation: none-host-local`，隔离提示固定为“无隔离边界、仅限本机”。它不再要求 Windows 主机或 WSL2，可直接在本机原生 Linux 运行，但不得把该运行方式描述为具有隔离或安全边界。
- 真机验收环境为原生 Ubuntu + RTX 5070 Ti 12GB，Python 3.12.13、torch 2.8.0+cu128，非 WSL。历史 v2/v3/v4/v5 仍按各自原 WSL2 身份与原摘要只读验证，不追溯获得 v6 语义。
- 固定命令清单以 `CLAUDE.md` / `README.md` / `CONTRIBUTING.md` 为准；不要新增 build/dev 命令，也不要引入框架（技术栈未定，需先关 `docs/OPEN_DECISIONS.md` 并写 ADR）。

## 6. 沿用的工作方式

上一轮跑通、建议继续沿用：

1. **主模型决策 + Codex 执行**：主模型切包、写派单提示词、验收；Codex 只做单包实现。
2. **先按原子变更划包**：同一 identity/digest/policy 变更涉及的策略文件、worker 期望与 profile 声明必须归入同一包；只有彼此独立的原子变更才按不相交文件集并行。有依赖的（如 model_candidate 依赖 motion 侧共享重算接口）必须串行。
3. **派单契约**：`do NOT git commit`；范围严格限定到指定文件；涟漪只报告不越界；每包自带收尾门（跑指定测试）。
4. **主模型独立复验**：不采信 Codex 自报的“全绿”，自己复跑测试 + `git diff --stat` 看改动范围是否越界。
5. **每轮修复后跑对抗复核**，专门找修复引入的新缺陷。前两轮各抓出 5 条和 3 条，说明这一步不是形式主义。
6. 后台长任务（完整套件、Codex 作业）**必须落盘到会话外的位置**——上一轮丢结果就是因为它们只存在于会话进程里。

## 7. 红线（不要越过）

- 这些代码全部在 `spikes/` 下，是 Gate F 前的**可丢弃预研**，任何生产包不得导入。
- 成功状态措辞是固定的：`LOCAL_TECHNICAL_PREFLIGHT_PASS` / `LOCAL_WORKBENCH_COMPLETED` / `LOCAL_MODEL_SPIKE_COMPLETED` / `LOCAL_MODEL_MOTION_DRAFT_COMPLETED` / `LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED`，且一律附 `GATE_F_NOT_EVALUATED`。
- 37 帧 bbox quad/affine 结果只能标 `research_draft`；不得描述为专业绑定、成品模型、mesh-delta、`.oc2d` 或 `.moc3`。
- `model-candidate` / `verify-model-candidate` **不生成也不得声称** ballot、paired outcome、`F-USABLE` 或 20 项 Gate F 结果。
- supporting weight 许可元数据仍不完整：禁止权重再分发、禁止入库、禁止产品使用。
- 不实现、解析、检查、fixture 或逆向 `.moc3`。
- 详细边界见 [CLAUDE.md](../../CLAUDE.md)、[docs/index.md](../index.md)、[docs/FEASIBILITY_SPIKE_PLAN.md](../FEASIBILITY_SPIKE_PLAN.md)。

## 8. 一句话给接手人

当前接手重点不是重跑已经闭环的 P0-1/P0-2，而是保持 P1-2 开放：用更多权利明确且不入库的样本调查 §13 的 N-F，在中性保真门通过前不得继续接受 motion 或其下游结果。不得据此推导任何 Gate F 结论。

## 9. 收口附录（2026-08-02，UTC）

> 本节由接手会话（macOS）追加。复核对象：HEAD `ee953de`；§3 所述“未提交”状态已过时——全部修复已作为 `450344c` 提交并经 PR #2 合并进 `main`，审计 journal 随之入库（P1-3、P2-2 闭环）。

- **P0-1 ✅**：完整 Pillow 套件在 macOS（Python 3.14.6 + Pillow 12.1.0 venv）**211 项 OK（16 项平台性跳过，0 失败 0 错误）**；文档 lint、smoke、preflight 均通过。独立第二次复跑（纯净 `git archive` 副本）同样全绿；无 Pillow 系统解释器下 213 项 OK（123 跳过）。注意：macOS 需将 `TMPDIR` 指向已解析路径（`/var`→`/private/var` 符号链接会被工作区硬化校验拒绝，属平台环境因素，非缺陷）；Pillow 按版本安装，锁定文件的 Windows wheel hash 在本机不适用。
- **P0-2 ✅**：两路独立对抗复核（Codex 与 Opus 5）+ 主模型抽验，一致判定 **F1 / F2 / F3 全部 CONFIRMED 闭环**。同时抓出 **8 条新发现（1 中 7 低）**，含一条与 F2 措辞相关：§2.2 “逐产物流式比对（峰值内存恒定）”与实现不符，实际是**产物粒度顺序处理**（单产物整体读入，受声明总量预算约束）。完整证据与新发现清单见 `.claude/review-records/2026-08-02-final-confirmatory-review/`。
- **P1-1 ✅**：全仓库 64-hex 常量 28 项逐条核对，workbench/motion/candidate 生产代码、schema、examples、tests 中无内嵌旧 entrypoint digest 期望；旧 digest `aedb9e25…` 全库零命中。
- **P1-2 后续状态**：active v6 已在原生 Linux GPU 真机完成 `model` 步，但 `motion` 被中性保真门拒绝；全链路仍开放，见 §13。
- **P2-1**：四个杂散文件在干净检出中不存在；若原 Windows 工作副本仍在，需在彼处清理。
- **下一轮待办**：新发现 N1–N8 的分诊与修复（N1 中危：attestation 摘要未入报告、`postprocess_algorithm` 缺 `.psd-postcorrect.v1` 后缀；N6 涉及 profile_id 是否升版，属决策项）。本轮审计修复循环至此收口。（后续状态见 §10。）

## 10. N 系列修复轮（2026-08-02，UTC，同日追加）

§9 所列新发现已完成一轮“分包修复 → 双路对抗复核 → 收尾修复”，全部改动仍在工作树**未提交**：

- **已闭环**：N1（attestation 摘要经 worker 返回、workbench 独立重验后并入报告；`postprocess_algorithm` 发布带 `.psd-postcorrect.v1` 后缀的实际执行标识；历史 v2/v3 不变且携带 attestation 会被拒）、N2（描述符与可信重算证据的 (sha256, byte_length) 在任何产物 I/O 前精确核对）、N3（`_png_facts` 解码前验画布 + 钉版加载 + 炸弹护栏）、N4（active v4 阈值改读 profile 的 31，附 `alpha_threshold_source`；v2/v3 保持 15 并标注 legacy 来源）、N5（新增 `.gitattributes` 保护全部 raw-digest 绑定路径）、N8（`purpose_created.py` 共享模块，生产者/验证器字节级等价实证）。双路复核确认闭环（记录：`.claude/review-records/2026-08-02-fix-round-adversarial-review/`）。
- **复核抓出的第二批缺陷已收尾修复**：D1/Codex#2（报告 `format_version` 升 0.4.0，loader 对历史 v2/v3 的 0.3.0 持久化报告按投影严格验证，v4 的 0.3.0 与未知版本给专门版本错误，附静态字节 fixture 回归）、Codex#3（attestation 组件设备必须精确等于顶层 `execution_device`，覆盖裸 `cuda`/`cuda:1`/混合设备负例）、D2（reason_codes 列表去别名）、D3（`MAX_IMAGE_PIXELS` 三处改共用进程级锁上下文管理器，含线程交错回归）、D4/D5（死参数与危险默认参数清除）、D6（纵深预算测试改名标注）、D7（本文档 §2.2 F2 措辞更正）、D8（non-active 文案）。
- **当轮显式留决策（后续状态见 §11）**：attestation 与 run/source/产物的绑定（Codex#1，中危——当轮 attestation 是可逐字复现的自述，非执行期密码学证明）与 N6（profile_id 升 v5 还是加 `attestation_revision`）。二者同属 digest 链决策域，已在后续一并定夺并实现。N7 按复核结论接受现状（loopback-only，实测 ~30 ms/帧）。
- **注意**：`format_version` 升 0.4.0 意味着本轮之前发布的**当时 active v4** workbench 报告需重新生成（历史 v2/v3 报告仍按 0.3.0 投影可验，符合仓库承诺）；当时尚无真实 GPU 运行产物，实际影响为零。v4 后续成为历史 profile，随后曾活跃的 v5 报告版本升为 0.5.0，见 §11。
- 验收状态：完整套件（Pillow venv）全绿、`preflight` `LOCAL_TECHNICAL_PREFLIGHT_PASS` + `GATE_F_NOT_EVALUATED`、文档 lint 通过（以主模型宿主机独立复跑为准；Codex 沙箱内 `test_gate_f_gui_server` 的 12 项 socket bind 失败为沙箱限制，宿主复跑通过）。P1-2 后来已在 active v6 的原生 Linux GPU 真机上推进通过 `model` 步，但 `motion` 被中性保真门拒绝，全链路仍开放，见 §13。

## 11. 历史 v5 digest 链与每次运行清单绑定（2026-08-02，UTC，同日追加）

§10 留下的两个决策已作为同一变更闭环。由于新的运行绑定改变了入口字节、报告语义和下游可消费身份，而仓库内当时尚无真实 v5 运行产物，本轮曾把 active profile 升为 `see-through.v3.nf4.1280.wsl2.source-preserve.v5`，同时把原 v4 profile 原字节归档为 `model_profiles/see-through-v3-nf4.source-preserve-v4.json`。v5 随后也已原字节归档；当前 active profile 是 §13 的宿主中立 v6，历史 v2/v3/v4/v5 均继续按各自原 WSL2 身份与原摘要只读验证，不追溯获得 v6 语义。

- **每次运行绑定**：受信父进程为每次 WSL2 调用生成一次性 challenge；v5 入口在上游脚本完整成功返回（含成功 `SystemExit(0/None)`）且 PSD 像素投影确实执行后，记录源图 SHA-256、最终产物清单及其摘要。非零退出或未完成 PSD 投影不会发布 attestation。
- **三次独立核对**：worker 消费 attestation 时逐项核对 challenge、源图摘要、声明清单和磁盘清单；固定 inventory/PSD 验证后再次从最终发布目录重算清单摘要；workbench 构建或重载报告时再从留存的 `model-output/input` 重算。任一产物在这些边界间变动均 fail-closed，报告保持 `model_used: false` 并使用有界 reason code。
- **资源与进程边界**：清单遍历与哈希有固定的累计字节、条目、目录、节点、深度和相对路径长度上限，父进程与 v5 入口使用同一套规则，attestation 排除文件不计入节点上限；challenge、attestation 路径与源路径通过父环境及 `WSLENV` 透传，不再出现在 `wsl.exe` argv。新增 v0.2 motion/candidate schema 和 profile identity，旧 v0.1 schema 保持不变，并加入立项文档 lint 的必需文件列表。
- **摘要冻结**：当时 active v5 入口 SHA-256 为 `8732db76c4fcf3f4bf7e94f3a206456ffbf9bd78ef773aa66d9b793c6f8f1ac5`（遍历边界修复后就地更新 v5，未升 v6），与当时 active profile 声明一致；归档 v4 profile SHA-256 保持 `d24de59690e0db2c64828e580eed8b00f939d5327b255ef59f1826f8cf582ae3`，v4 入口保持 `ae4d26b042b8b15e7bdcfdacd11c50b16d97c1ccf19aad94162dd67046e1642f`，device policy 保持 `569e0ced8bcc4b144bfc787e0e37f2d90fc263081ceac3c063eabf26ce1c14df`。
- **能力边界不变**：该绑定证明受信父进程看到的源图、attestation 和发布产物清单彼此一致；它不证明被钉死的 entrypoint 确实执行，不是密码学执行证明或可信执行环境保证。完全控制 WSL2 worker 环境者仍可为自造产物生成自洽清单。所有结果继续是 `review_required` 与 `GATE_F_NOT_EVALUATED`，不证明模型质量、PSD 外部互操作、`.oc2d`、专业绑定或 Gate F 可行性。
- **真机门后续状态**：P1-2 已迁移到宿主中立 active v6 的原生 Linux 运行时；`model` 已在 GPU 真机通过，`motion` 被中性保真门拒绝，故 `model → motion → model-candidate → verify-model-candidate` 仍未跑通、仍未关闭，见 §13。

## 12. 遍历/哈希边界收口与静态 fixture 可移植性发现（2026-08-02，UTC，同日追加）

> 本节由 Linux 接手会话追加（原生 Ubuntu，Python 3.14.4 + Pillow 12.1.0 venv，**非 WSL**）。基线 `93aec48`（PR #4 已合并）。对象仍是 `spikes/` 下 Gate F 前可丢弃预研，全部状态继续 `GATE_F_NOT_EVALUATED`。

### 12.1 §9/§11 留下的两条非阻塞加固候选已闭环

两条都记在 `.claude/review-records/2026-08-02-v5-binding-review/README.md` 的"仍开放"末尾，均为基线既存、非当轮引入。本轮一并收口，**未触碰任何被摘要钉死的文件**——当时 active v5 入口仍为 `8732db76c4fcf3f4bf7e94f3a206456ffbf9bd78ef773aa66d9b793c6f8f1ac5`、当时 active profile 仍为 `e53049e5885419bd9d1d5c70d8b2514226ddcab9c33cdc8750d3f206401e4009`，实算与声明一致。当前 active v6 身份见 §13。

- **加固 A（workbench 清单遍历无界）**：`model_workbench.py` 的 `_indexed_files` 全树遍历此前只靠"首个非常规节点即拒 + 事后集合比对"兜底，没有节点/目录/深度/相对路径长度上限。该函数在**重载已持久化报告**时会重走 `model-output`，此时 worker 进程内的 `_inventory` 边界并不会再次施加，因此这是真实的资源耗尽面。现改为复用 worker 的同一组常量（`MAX_MODEL_ARTIFACT_MANIFEST_{DEPTH,DIRECTORIES,NODES,ENTRIES}` 与 `MAX_MODEL_ARTIFACT_RELATIVE_PATH_BYTES`），并在遍历期即 fail-closed。固定 profile 的合法产物树为 55 文件 / 57 节点 / 深度 2，远在 256 / 320 / 8 之内；且同一棵树在 worker 边界已由 `_inventory` 施加同样上限，故此改动是**对齐**而非收紧。
- **加固 B（worker 清单重算整文件读入内存）**：`_artifact_manifest` 与 `_inventory` 此前用 `read_bounded_file` 把单个产物整体读入再哈希，单文件峰值可达 `MAX_MODEL_RESULT_BYTES`（512 MiB）。**被钉死的 v5 入口本就是分块流式**（`_sha256_file`，1 MiB 块），所以这是父进程侧的单边不对称。新增 `_bounded_artifact_digest(path, maximum)`，按 1 MiB 块流式哈希并返回 `(sha256, byte_length)`，边界语义与 v5 入口逐条对齐；同时用 `is_symlink()/is_file()` 预检加打开后 `fstat` 的 `S_ISREG` 复核，比原先只靠 `read_bounded_file` 的符号链接预检更严。**产出不变**（同样的 `sha256`/`byte_length`），因此不影响任何既有摘要或 attestation 绑定。
- **回归测试**：worker 侧新增 5 项（分块峰值上限、超预算增长拒绝、符号链接拒绝、`_artifact_manifest` 与 `_inventory` 的多块哈希与 `hashlib` 逐字节一致）；workbench 侧新增 6 项（合法产物树仍被接受，深度/目录数/节点数/条目数/路径长度五类越界均在**哈希任何产物之前**拒绝）。另有两项既有测试的守卫从 `read_bounded_file` 改挂到 `_bounded_artifact_digest`——`_artifact_manifest` 已不再调用前者，不改守卫会让这两项**静默失效**。新增的越界用例经"把上限调至极大后必须转为失败"实测确认非空转。

### 12.2 新发现 L1：静态 persisted-report fixture 依赖 PNG 编码器实现（中危，测试可移植性）

**这是基线既存缺陷，与 12.1 的改动无关**——在改动前的基线 `93aec48` 上即可复现。

`tests/test_gate_f_model_workbench.py` 的三项静态字节 fixture 测试在本机报错，均为同一条根因：

| 测试 | 结果 |
|---|---|
| `test_loads_static_v03_persisted_reports_for_historical_v2_and_v3`（v2 与 v3 两个 subTest） | ERROR |
| `test_loads_static_v04_persisted_report_for_historical_v4` | ERROR |

报错一律是 `persisted model workbench report does not match validated evidence`。逐字段比对显示**差异全部集中在图层 PNG 产物的 `sha256`**：三个冻结报告都内嵌 `7eb0231bb990fadecd90787c8f069148f78dab682eb386b3990312457a10801c`，而本机重算得到 `c5248eb554b555edb7066f8af05665c635f869b514eda270fcc2f1be6a944b19`。

根因：fixture 的 `_png()` 用 `PIL.Image.save(format="PNG")` 生成纯色图，**PNG 字节取决于 Pillow 链接的 zlib 实现**；而冻结报告把这些字节的摘要写死了。本机 Pillow 12.1.0 / zlib 1.3.1 下遍历 `compress_level` 0–9 与默认值均无法复现 `7eb0231b…`，可确认冻结值来自另一套 zlib 构建。v4 冻结块由 `057fb2d`（PR #4）引入，该 PR 只在 macOS 上验证过。

判定：**测试 fixture 可移植性缺陷，不是生产代码缺陷**。loader 按设计独立重算并逐字段比对，行为正确；错的是 fixture 把环境相关的编码器输出当成了不变量。据此，仓库固定命令 `python -m unittest discover` 在非录制环境下不可能全绿，与 §4 P0-1"全绿，只允许平台性跳过"的验收标准冲突。

**本轮已修复**。曾考虑的另一方案是让冻结块的产物摘要在测试期从实际 fixture 代入，但那会让摘要比对退化成同义反复——被比对的两侧都来自同一次重算，等于取消了对这些字段的校验。因此采用**把 fixture PNG 本身固化为常量字节**：

- `_png()` 不再调用 `PIL.Image.save()`，改为解码两个内嵌常量（`_FIXTURE_RGBA_PNG_ZLIB_BASE64` / `_FIXTURE_GRAYSCALE_PNG_ZLIB_BASE64`），编码方式与文件既有的 `_legacy_workbench_report_v03_bytes` 同一约定（zlib + base64）。纯色 1280×1280 图的 PNG 再经 zlib 压缩后仅 193 / 142 字节，base64 后 260 / 192 字符，代价可忽略。固化后两张图在任何环境都解码为 `1280×1280` 的 `RGBA` / `L`，摘要恒为 `c5248eb5…` / `18fe745e…`。
- 三个冻结报告块（`LEGACY_V2_…_V03`、`LEGACY_V3_…_V03`、`LEGACY_V4_…_V04`）据此重新冻结。它们仍是提交进仓库的**固定字节**、仍是 0.3.0 / 0.4.0 旧格式，因而完全保留"旧格式报告必须仍能被 loader 接受并投影到 0.5.0"的回归价值；差别只是这些字节从此在所有环境可复现。测试自带的版本/结构断言（`format_version`、`profile_id`、`entrypoint_attestation` 有无、`alpha_threshold` 为 31 等）未做任何放宽。
- 注：`tests/test_gate_f_model_worker.py` 的 `_model_png()` 仍用 Pillow 编码。它不参与任何冻结摘要比对，本轮未改动；若日后要为 worker 侧也引入静态字节 fixture，需先同样固化。

### 12.3 本轮验收状态（Linux 宿主）

| 门 | 结果 |
|---|---|
| 立项文档 lint | ✅ 38 Markdown / 44 JSON |
| 标准库合成编排 smoke | ✅ `status=succeeded` |
| 本地技术预检 | ✅ `LOCAL_TECHNICAL_PREFLIGHT_PASS` + `GATE_F_NOT_EVALUATED` |
| 完整 Pillow 套件（12.1 改动后、12.2 修复前） | 259 → **270 项**（+11 新测试），仍恰好 3 ERROR（全部为 L1），16 跳过——即加固改动**未引入任何新失败** |
| 完整 Pillow 套件（12.1 + 12.2 全部落地后） | 见 12.5 |

三次套件运行的错误集合逐项一致（同样三项静态 fixture 测试），据此可把 L1 与本轮加固改动完全解耦归因。

### 12.4 P2-1 杂散文件已处置

§4 所列四个未跟踪文件在本工作副本仍存在，且**均未被 `.gitignore` 覆盖**，离误提交只差一个 `git add -A`。本轮已确认内容：

| 文件 | 实测内容 |
|---|---|
| `NUL`(63 B) | 一行 WSL socket 报错，`2>NUL` 重定向在 POSIX shell 下落成真实文件 |
| `xaa`(0 B) | 空文件，`split` 残留 |
| `converted.png`(380 KB) | 500×500 8-bit RGB PNG，来源不明 |
| `-s`(1 MB) | **safetensors 格式权重分片**，首部为 `{"__metadata__":{"format":"pt"},"add_embedding.linear_1...` |

后两者按 CLAUDE.md 分属"用户艺术资产"与"未批准权重"，**禁止入库**。处置方式选择**移出仓库而非删除**：四个文件已整体移到工作副本外的 `~/oc2d-stray-2026-08-02/`，误提交风险即刻消除，同时不销毁来源与权利不明的素材（删除不可逆，且 `-s` 与 `converted.png` 的归属未经确认）。仓库工作树现已干净。

### 12.5 当时仍开放（后续状态见 §13）

- **P1-2 真机链路**：原生 Ubuntu + RTX 5070 Ti 已在后续 active v6 上执行 `model` 并通过；不再需要 `wsl.exe` / `powershell.exe`。`motion` 被中性保真门拒绝，故 `model → motion → model-candidate → verify-model-candidate` 全链路仍未跑通、P1-2 仍未收口，见 §13。

## 13. 宿主中立 v6 真机执行证据与开放门（2026-08-04）

> 本节只记录 `spikes/gate_f_runner` 可丢弃预研的真机聚合证据与移交状态。生产包不得导入这些代码；所有成功状态一律附 `GATE_F_NOT_EVALUATED`，本节不构成任何 Gate F 结论。

### 13.1 active 身份迁移与能力边界

- active `profile_id` 为 `see-through.v3.nf4.1280.source-preserve.v6`，身份已宿主中立，id 不再包含 `wsl2`；对应迁移决策见 [ADR 0002](../adr/0002-native-linux-model-runtime.md)（Proposed）。
- runtime 固定为 `kind: native-linux`、`isolation: none-host-local`、`isolation_notice: "无隔离边界、仅限本机"`。该路径没有隔离边界或安全边界，只能在本机使用。
- device `policy_id` 为 `see-through.v6.nf4-marigold-bounded-offload.v2`。
- v5 profile 已原字节归档；历史 v2/v3/v4/v5 继续按各自原 WSL2 身份与原摘要只读验证，不追溯获得 v6 语义。
- 运行绑定只证明受信父进程看到的 challenge、源摘要、attestation 与发布产物清单之间的一致性；它不证明被钉死的 entrypoint 确实执行过，不是密码学执行证明或可信执行环境保证。

### 13.2 环境复现缺口 N-A–N-D

以下四项都与宿主无关，在 Windows + WSL2 上同样缺失，不能把它们误归因为原生 Linux 移植问题：

- **N-A · profile 未记录 `sys.path` 前提**：入口与上游脚本分别执行 `from modules…` / `from utils…`，但两者都位于 `common/`；上游只把 `<code>/inference` 加入 path，而 `inference/modules` 不存在。v6 已补 `runtime.python_path_entries: ["common"]`，并在受信探针内实算 realpath，校验该条目确实生效。
- **N-B · 钉死依赖清单不自足**：`common/utils/cv.py` 顶层 import `pycocotools`，原 `dependencies_sha256` 无法锁住它。v6 已补 `pycocotools==2.0.11`，新清单摘要为 `b14584b1…`。
- **N-C · scheduler 绕开 profile 本地目录声明**：scheduler 走硬编码 repo id `frankjoshua/juggernautXL_version6Rundiffusion` 与 Hugging Face 缓存，并指定 `subfolder="scheduler"`；它不走 profile 声明的 `local_dir_relative_to_code_root`。
- **N-D · 固定 commit 下载不生成离线 ref**：离线解析需要 `refs/main`，按固定 commit 下载不会生成该 ref。

N-A/N-B 已按上述方式补齐；N-C/N-D 仍是复现环境必须显式处理的缺口，不能因本次真机成功而视为 profile 已完全自足。

### 13.3 v5 的两个真机功能缺陷与 v6 修复

决定性结论：此前 active v5 从未在任何真机上端到端跑通过。

1. **`_execution_aware_device` 只读根钩子**：在 pinned accelerate 1.13.0 下，`accelerate.cpu_offload(vae, execution_device="cuda:0")` 生成 1 个根模块钩子 `execution_device=None` 与 124 个叶子子模块钩子 `execution_device=cuda:0`。v5 入口只读根钩子，得到 `None` 后回落到原始实现并返回 `meta`。上游 `marigold_depth_pipeline.py:321` 的 `img.to(device=vae.device)` 因而把输入变成 meta，forward 最终报 `NotImplementedError: Cannot copy out of meta tensor; no data!`。
2. **校验静默丢弃 `None`，掩盖缺陷一**：`_hook_execution_devices` 用 `if … is not None` 过滤，`_validate_effective_policy` 最终只看到 `["cuda:0"]` 并放行。它实际断言的是“存在的钩子都指向 CUDA”，而不是“每个钩子都有执行设备且指向 CUDA”，属于过滤掉缺失值再断言造成的假阳性。

修复只新增 v6 入口与策略文件；v5 入口和策略逐字节未改，v5 声明摘要没有漂移。v6 的正确校验语义是：根钩子 `None` + 叶子 `cuda:0` 时放行并得到真机正常形状；没有任何非 `None` 执行设备时拒绝；出现非 CUDA 设备时拒绝，且两种拒绝使用可区分的错误。

### 13.4 v6 原生 Linux 真机验收

真机环境为原生 Ubuntu + RTX 5070 Ti 12GB，Python 3.12.13、torch 2.8.0+cu128，非 WSL。仅记录允许移交的聚合数字：

| 项目 | 真机结果 |
|---|---|
| `model` 命令 | 退出码 0；`LOCAL_MODEL_SPIKE_COMPLETED` + `GATE_F_NOT_EVALUATED` |
| 总耗时 | 406s（layerdiff 378s + marigold 19s + psd 4s） |
| 峰值显存 | 6.29GB / 12GB |
| 固定产物集合 | 55/55，与 `_expected_output_uris()` 完全一致；0 缺失、0 多余 |
| attestation | challenge 与本次运行一致；`execution_device: cuda:0`；`psd_projection_verified: true` |
| VAE 设备证据 | component storage=`["meta"]`，execution hook=`[None, "cuda:0"]`；v6 正确放行，验证来自真实数据而非 mock |
| worker 结果 | `WORKER_NATIVE_OK`；记录仍为 `GATE_F_NOT_EVALUATED` |
| 语义层 | 13/13 非空；face 15.30%、head 16.37%、mouth 0.03% |

worker 对 face/head/mouth 必须非空的校验一度被怀疑过严；真实风格插画验证表明，手绘几何合成图会让 face 恒为空，这是输入质量问题，现有校验合理，无需放宽。

### 13.5 P1-2 部分推进、仍未收口

P1-2 文档化顺序仍是 `model → motion → model-candidate → verify-model-candidate`。本轮首次在真机推进到第 2 步：

- `model` 已通过，状态为 `LOCAL_MODEL_SPIKE_COMPLETED` + `GATE_F_NOT_EVALUATED`；CLI 在原生 Linux 上没有误拒。
- `motion` 被中性保真门拒绝；`generate_model_motion_draft` 抛出 `model motion draft requires a fidelity-passing active model profile`。三项门限均未达标，因而状态为 `review_required`，不是仅有个别指标擦边未过。
- `model-candidate` 与 `verify-model-candidate` 没有越过该拒绝继续执行。P1-2 因此只是部分推进，全链路仍然开放，绝不能记为已跑通或已关闭。

### 13.6 显著开放项 N-F：中性保真门在当前真机样本下不可达

N-F 已登记为 [R-021](../RISK_REGISTER.md)，是当前阻断 P1-2 收口的最重要开放项。三项 pass 条件与真机实测如下：

| 指标 | pass 条件 | 真机实测 | 判定 |
|---|---|---|---|
| `source_visible_coverage_ratio` | 恰好 `== 1.0` | `0.982144` | 不通过 |
| `source_rgb_exact_ratio` | `>= 0.995` | `0.984104` | 不通过 |
| `source_rgb_mae` | `<= 0.5` | `3.921186` | 不通过，约为限值的 7.8 倍 |

报告同时记录 `alpha_threshold = 31`、`status = review_required`。

- **已测量事实一**：`reconstruction_visible_pixel_count = 785770` 与 `source_visible_covered_pixel_count = 785770` 相等；结合 `source_visible_pixel_count = 800056` 与 `source_visible_omission_count = 14286`，说明重建结果是源可见集合的子集，没有任何越界新增，唯一差异是 14286 个未被分配的源可见像素。漏像素不是单纯边缘抗锯齿现象：其中位于源轮廓 2px 内的仅 21.2%，即约 79% 不在该边缘带内；漏区共有 1,267 个连通块，中位大小 1px，但最大块为 4,838px，存在成片空洞。
- **已测量事实二**：`source_rgb_channel_mae = [3.92266, 3.919734, 3.921163]`，三通道彼此相差不到 0.003。这一测量形态与“整片像素缺失、在 RGB 比较中按黑计入”一致，而与色彩变换、gamma 偏移或通道级失真不一致。
- **直接复测方法与结果**：先使用同一次真机运行的 `trusted-model-source.png` 与 `model-output/input/input/reconstruction.png`，按 `alpha_threshold = 31` 复现报告统计，得到 `source_visible = 800056`、`source_visible_coverage_ratio = 0.982144`、`source_rgb_exact_ratio = 0.984104`、`source_rgb_mae = 3.921186`，与报告逐位一致，从而自证产物配对与统计方法正确。随后只把统计掩膜换为 covered 掩膜（源可见 ∩ 重建可见，共 785770px）重新测量；该 RGB-only 比较不含 alpha，得到 `rgb_mae = 0.000000`、`exact_ratio = 1.000000`，逐通道 MAE 为 `[0.0, 0.0, 0.0]`。
- **测量结论（已证实）**：三项门限失败全部且仅仅来自 14286 个未被分配到任何语义层的源可见像素，不是三个独立问题。`source_rgb_mae = 3.921186` 完全由缺失像素贡献，覆盖像素的贡献恰好为零。`3.921186 × 800056 ÷ 14286 ≈ 219.6` 仍表明缺失像素在源侧的平均通道值约为 219.6，但该算术校验现在只是与直接测量一致的旁证，不再是根因归因的依据。

source-preserve 的原图 RGB 回填机制在本次运行中、在全部覆盖像素上工作正确，RGB-only 零误差。这不证明蒙版语义正确、不证明隐藏区域真实、不构成任何 Gate F 结论。

当前判定是内容/模型行为问题，不是原生移植缺陷：同一模型、同一权重、同一 pinned 依赖在任何宿主上都会产生同样的分层结果。中性保真门按设计拒绝，本轮没有放宽。

> **后续定性修正**：本段是 §13 当时基于 coverage/RGB 聚合值作出的初步定性；[§14](#14-n-f-根因诊断清理后-alpha-标度与真实零分配2026-08-04) 已用逐层 alpha 将 14286 个门视角漏失像素拆成两种根因。不得再把全部 14286 个像素统称为内容/模型行为问题。

N-F 的待查问题现已收敛为唯一一项：为什么有 14286 个源可见像素未被分配到任何语义层。单一样本仍无法判断该问题是：(a) 本次输入特有；还是 (b) 该门对当前模型普遍过严。下一步仍需使用更多权利明确且不入库的样本加以区分；本轮对此没有下结论，也不得预设以放宽阈值解决。

> **后续决策指针**：中性保真门此后不再阻断 motion，降级范围、保留的硬门和风险边界见 [§15](#15-中性保真门降级为警告与透明背景输入前提实测2026-08-05)。

### 13.7 派单方法论教训

`policy_id` 升版是跨文件原子变更：策略文件发布什么、worker 期望什么、profile 声明什么必须同时更新。本轮却曾按“文件归属”把它切到两个包，导致首次真机验收在 attestation 校验处失败。后续包边界必须按原子变更切分，而不是按文件集切分；可并行性只能在原子变更彼此独立之后判断。

### 13.8 继续开放的移交项与红线

- P1-2 保持开放：先用更多权利明确样本调查 N-F；只有 motion 合法通过后，才继续其下游步骤。
- N-C/N-D 保持为环境复现缺口；不得把一次真机成功写成 profile 已完全自足。
- 原生 runtime 没有隔离边界、仅限本机；attestation 绑定不是密码学执行证明或可信执行环境保证。
- 权重许可元数据仍不完整：禁止再分发、禁止入库、禁止产品使用。不得记录用户素材、客户内容、私有路径或权重本体。
- 不实现、解析、检查、fixture、承诺或逆向 `.moc3`。任何成功状态继续附 `GATE_F_NOT_EVALUATED`。

## 14. N-F 根因诊断：清理后 alpha 标度与真实零分配（2026-08-04）

> 本节是对单次 `workspaces/gate-f-spike/run.native-v6/` 产物的只读诊断。没有修改 `spikes/`、`workspaces/`、入口、profile 或验收门；没有授权或建议改变任何阈值；不推导任何 Gate F 结论。状态仍为 `GATE_F_NOT_EVALUATED`。

### 14.1 方法、输入与自证（M0–M1）

分析只读取 `trusted-model-source.png`、`model-output/input/input/reconstruction.png`、`workbench-report.json` 以及固定入口 `spikes/gate_f_runner/model_entrypoints/see_through_v3_nf4_source_preserve_v6.py`。语义层名从入口 AST 的 `PART_NAMES` 常量读取，共 23 层；没有按目录猜测，也没有把其它 PNG 或深度图纳入 union。逐层取清理后 alpha 的像素最大值得到 `union_alpha`。

- **M0 报告复现**：源侧与重建侧均使用严格 `alpha > 31`。Pillow 掩膜/统计与独立逐像素纯 Python 循环都得到 `source_visible=800056`、`reconstruction_visible=785770`、`covered=785770`、`omitted=14286`、`exact_pixels=787338`。coverage 原值为 `0.9821437499375044`，六位值 `0.982144`；exact 原值为 `0.9841036127471077`，六位值 `0.984104`。RGB 通道绝对误差和为 `[3138348, 3136007, 3137150]`，通道 MAE 原值为 `[3.922660413771036, 3.9197343685941983, 3.921163018588699]`，六位值 `[3.922660, 3.919734, 3.921163]`；三通道平均原值 `3.921185933651311`，六位值 `3.921186`。这些值与 `workbench-report.json` 逐项一致。
- **M1 union 一致性**：23 层求得的 `union_alpha` 与 `reconstruction.png` alpha 在 `1280×1280` 全画布逐像素相等。ImageChops 差分直方图的非零像素数为 `0`，独立逐字节比较的差异数也为 `0`；差异 extrema 为 `[0,0]`，bbox 为空。由此确认读取的是入口实际使用的层集合与对应重建产物。

### 14.2 判定性拆分（M2）

漏失掩膜定义为“源 alpha `>31` 且重建 alpha `<=31`”。`union_alpha.histogram(mask=omitted_mask)` 与独立逐像素 Counter 均得到 `14286`，且 `32..255` 桶总数为 `0`。

| 漏失像素上的 `union_alpha` | 像素数 | 占 14286 的比例 | 直接支持的判定 |
|---|---:|---:|---|
| `0` | 12718 | 89.024220% | 支持 H2：按固定清理公式，所有语义层清理前 alpha 均 `<=31` |
| `1..31` | 1568 | 10.975780% | 支持 H1：至少一层在清理后仍有非零 alpha，但第二次 `>31` 判定将其视为不可见 |

M2 分组直方图为：`0:12718`、`1–8:420`、`9–16:473`、`17–24:385`、`25–31:290`。完整 `0..31` 精确直方图（按 alpha 从 0 到 31）为：

```text
[12718, 66, 42, 71, 0, 58, 72, 54, 57, 77, 52, 77, 0, 77, 67, 74,
 49, 58, 71, 44, 43, 0, 67, 43, 59, 31, 31, 79, 63, 0, 36, 50]
```

因此 H1 与 H2 在这个单一样本中同时成立，不能二选一。H2 占门视角漏失像素的多数；H1 的 1568 个像素则证明 §13.6 所称“全部未被分配到任何语义层”不够精确：这些像素已有非零层 alpha，只是清理后标度上的值没有通过同数值的第二次可见性判定。另有算术交叉检查：`exact_pixels=787338=covered 785770 + H1 1568`；本样本 H1 子集的重建 RGB 全部精确，H2 子集才贡献 RGB mismatch 与 MAE。

### 14.3 H1 原始层 alpha 反解（M3）

对 1568 个 `union_alpha=1..31` 像素使用名义逆变换 `round(cleaned×224/255)+31`，得到最大层清理前 alpha 范围 `32..58`。按原始 alpha 从 32 到 58 的计数依次为：

```text
[66, 42, 71, 58, 72, 54, 57, 77, 52, 77, 77, 67, 74, 49,
 58, 71, 44, 43, 67, 43, 59, 31, 31, 79, 63, 36, 50]
```

单独使用近似逆变换时应保留 ±1 的取整不确定性。作为复核，本轮还对入口的精确整数正向公式穷举 `0..255`：本样本实际出现的 cleaned 值 `1,2,3,5,6,7,8,9,10,11,13,14,15,16,17,18,19,20,22,23,24,25,26,27,28,30,31` 各自只有一个正向前像，依次对应原始值 `32..58`；未出现的 `4,12,21,29` 本来就没有整数前像。磁盘层 PNG 已被覆盖为清理后 alpha，故近似逆的不确定性仍在证据说明中保留，不把反解值表述为对清理前文件的直接读取。

### 14.4 两组的源 alpha 形态（M4）

以下分桶由源图 alpha 直方图和逐像素 Counter 双重核对；“半透明”仅指数值 `32..254`，不对图像内容作语义判断。

| 组 | 32–63 | 64–127 | 128–191 | 192–254 | 255 | 中位数 |
|---|---:|---:|---:|---:|---:|---:|
| H2，`union_alpha=0`（12718） | 382 | 938 | 414 | 38 | 10946 | 255 |
| H1，`union_alpha=1..31`（1568） | 562 | 131 | 377 | 160 | 338 | 139 |

H2 组中源 alpha `255` 有 `10946`（86.066992%），`32..254` 有 `1772`（13.933008%），源 alpha 范围 `32..255`；它以实心内部像素为主，不是半透明边缘主导。H1 组中源 alpha `255` 有 `338`（21.556122%），`32..254` 有 `1230`（78.443878%），范围同为 `32..255`；它以半透明像素为主，但仍包含 338 个源侧实心像素，不能将整组等同于轮廓抗锯齿。

### 14.5 连通块与语义层集中度（M5–M6）

M5 以 4 邻接作为与 §13.6 的 `1267` 块、最大 `4838px` 记录一致的主口径；BFS 与独立并查集得到完全相同的尺寸序列，所有中位数使用 `statistics.median`。

| 掩膜 | 连通块数 | 尺寸中位数 | 最大块 | 像素和 |
|---|---:|---:|---:|---:|
| 全部漏失 | 1267 | 1 | 4838 | 14286 |
| H2，`union_alpha=0` | 875 | 1 | 4740 | 12718 |
| H1，`union_alpha=1..31` | 1055 | 1 | 23 | 1568 |

整体最大 `4838px` 块不是纯组块：其中 H2 为 `4740`（97.974370%），H1 为 `98`（2.025630%），所以应判为 H2 主导的混合块。8 邻接敏感性复核不改变该最大块及其构成：全部漏失为 `520` 块/中位数 `1`/最大 `4838`，H2 为 `439` 块/中位数 `2`/最大 `4740`，H1 为 `898` 块/中位数 `1`/最大 `32`；像素和仍分别为 `14286/12718/1568`。

M6 对每个 H1 像素在 23 层中取最大清理后 alpha。逐像素 argmax 与逐层“alpha 等于 union”图像掩膜复核一致，全部 `1568` 个像素都只有一个最大层，没有并列：`back hair=830`（52.933673%），`topwear=738`（47.066327%），其余 21 个 `PART_NAMES` 层均为 `0`。H1 在本样本中完全集中于这两个语义层。

### 14.6 判定、边界与待决策项

- **H2 为多数且有实心大块**：12718 个像素上 `union_alpha=0`，支持“所有层清理前 alpha 均不高于 noise floor”的 H2；其中 86.066992% 的源 alpha 为 255，且最大整体漏块由 H2 像素占 97.974370%。这部分是当前模型分层结果在入口有效 alpha 定义下的真实零分配，不是 H1 的标度现象。由于清理前层 alpha 已被覆盖，`union_alpha=0` 不能进一步区分清理前 alpha 是 0 还是 `1..31`。
- **H1 是独立存在的我方标度不一致**：1568 个像素已有清理后非零层 alpha，却因源 raw alpha 与重建 cleaned alpha 共同套用数值 31 而被门计为漏失。该子集应从“内容/模型行为问题”中移出并登记为 postprocess/验收门标度不一致；它是待决策项，任何处置都需要独立决策与 ADR。本轮不改变，也不建议改变阈值。

将“完整修正 H1 标度不一致、1568 个像素改计为已覆盖”仅作为反事实测算，不改变任何阈值，三项指标将是：

| 指标 | 当前 | 完整修正 H1 后 | 门限 | 修正后是否通过 |
|---|---:|---:|---:|---|
| `source_visible_coverage_ratio` | `0.982144` | `0.984104`（`787338 / 800056`） | 恰好 `== 1.0` | 仍不通过 |
| `source_rgb_exact_ratio` | `0.984104` | `0.984104`（不变） | `>= 0.995` | 仍不通过 |
| `source_rgb_mae` | `3.921186` | `3.921186`（不变） | `<= 0.5` | 仍不通过 |

H1 修正只影响 coverage；exact ratio 与 RGB MAE 完全不变，因为这 1568 个像素的重建 RGB 本来就精确，且 §14.2 已交叉验证 `exact_pixels 787338 = covered 785770 + H1 1568`。修正后的 coverage 为 `787338 / 800056 = 0.984104`，与当前 exact ratio 相同，因为两者此时对应同一个像素集合；它仍不满足 coverage 必须恰好等于 `1.0` 的门限。因此 H1 仍是独立成立、需要单独决策与处置的正确性问题，但修正它不能解除保真门阻塞，也不在解除该阻塞的关键路径上；在是否能够解除本次阻塞的意义上，剩余且决定性的原因完全是 H2 的 12718 个真实零分配像素。下一轮推动 P1-2 的诊断重点应放在 H2，不得预期处置 H1 会使三项门限通过。

- **不外推**：这些比例只来自单一样本，不能推出其它输入上的 H1/H2 比例，不能推出该门对任何输入的可达性，也不能概括模型的一般行为。H2 的样本特异性仍需用更多权利明确且不入库的样本测量。P1-2 保持开放，不得记为跑通或关闭；本节不产生任何 Gate F 结论，状态仍为 `GATE_F_NOT_EVALUATED`。
- **运行与许可边界不变**：原生 runtime 无隔离边界、仅限本机；权重禁止再分发、禁止入库、禁止产品使用。继续禁止实现、解析、检查、fixture、承诺或逆向 `.moc3`。

## 15. 中性保真门降级为警告与透明背景输入前提实测（2026-08-05）

> 本节记录用户拍板后的 motion 阶段行为及第二个真机样本的聚合测量。该决策把阻塞降级为记录，不表示质量提升、风险缓解或风险消失，也不表示中性保真门已通过。P1-2 仍未跑通、仍未关闭；产物仍只能标为 `research_draft`，全部成功状态继续附 `GATE_F_NOT_EVALUATED`，本节不推导任何 Gate F 结论。

### 15.1 决策与未改范围

用户拍板放松中性保真门，选定做法是“降级为警告”，而不是改变阈值。以下内容逐项未改：

- `source_visible_coverage_ratio` 的 pass 条件仍恰好为 `1.0`；
- `source_rgb_exact_ratio` 的 pass 条件仍为 `>= 0.995`；
- `source_rgb_mae` 的 pass 条件仍为 `<= 0.5`；
- 三项指标的全部测量逻辑以及 `neutral_fidelity.status` 的判定规则均未改动。

### 15.2 motion 行为、保留硬门与 candidate 边界

- motion 不再因 `neutral_fidelity.status = review_required` 而拒绝生成。门未通过时，motion 报告的 `quality.review_items` 追加 `FIDELITY_GATE_NOT_PASSED`，携带 coverage、exact ratio、RGB MAE 三项实测值及各自门限；门限从上游 `neutral_fidelity` 报告自身的 `pass_thresholds` 读取，不在告警生成逻辑中硬编码。motion 报告的 `quality.status` 仍恒为 `review_required`，产物仍只标 `research_draft`。
- active 模型 profile 身份不匹配或 `model_used` 证据不成立仍然硬失败。原因是这项证据保护此前由保真门“兼职”承担：受信源图缺失的运行之所以得到 `neutral_fidelity.status = review_required`，是因为源证据缺失、无从比对，而不是模型质量差。降级时如果不单独补回 `model_used` 硬检查，证据不可信的运行就会被放行。`test_import_without_retained_trusted_source_cannot_activate_model` 保持“不得生成 motion 且不得创建 motion-draft 目录”的回归守卫。
- candidate 侧的两处保真检查没有降级，`model-candidate` / `verify-model-candidate` 仍要求保真通过。candidate v0.3 schema 的 `quality.review_items` 是固定 const，没有自由扩展点；若要把 candidate 侧也降级，必须另行决策并提升 schema 版本。

### 15.3 验收与边界

- 宿主全量套件共 297 项，全部通过。Codex 沙箱内 `test_gate_f_gui_server` 的 12 项 socket bind 错误是已知平台限制，与 §10 的记录一致；宿主复跑通过。
- motion 已在 `run.model-20260804-232357181` 上成功写出 `LOCAL_MODEL_MOTION_DRAFT_COMPLETED` + `GATE_F_NOT_EVALUATED`。这只证明该次本地预研 motion 草案完成，不表示 P1-2 跑通或关闭，不构成任何 Gate F 结论。
- 原生 runtime 仍无隔离边界、仅限本机；权重禁止再分发、禁止入库、禁止产品使用。继续禁止实现、解析、检查、fixture、承诺或逆向 `.moc3`。

### 15.4 第二个真机样本：透明背景输入前提

模型路径要求输入具备透明背景，否则中性保真门在结构上不可能通过。这里记录的是第二个真机样本中“不透明背景仍被源侧计为可见、而语义层只覆盖角色”的特定结构，不外推到所有输入。证据来自用户提供的不透明底输入，运行 `run.model-20260804-232357181`：

- `source_visible = 1638400`，恰好等于整张 `1280×1280` 画布，即输入完全不透明、没有 alpha；
- 漏失 `409251`（`24.98%`）；
- 用 `diagnose-fidelity` 拆分：H2（`union_alpha == 0`）=`406305`，占漏失的 `99.28%`；
- 该 H2 组的源 alpha 分桶为 `32-63:0 / 64-127:0 / 128-191:0 / 192-254:0 / 255:406305`，即全部恰为 `255`；
- 漏失区连通块仅 `29` 块，最大一块 `346498 px`。

判定：语义层只分割角色、不覆盖背景；而保真门把源图不透明的每个像素都算作应被覆盖，于是整个背景被判为漏失。这是输入前提不满足，不是模型质量问题。

对照第一个已抠背景样本 `run.native-v6`：源可见 `800056`（画布的 `49%`）、漏失 `14286`（`1.79%`）、漏失连通块 `1267` 块且最大 `4838`、H2 组源 alpha 均值 `233.5`。两个样本形态完全不同。

当前可用性缺口是 GUI 与文档都没有提示“输入需已抠背景”这个前提，下一个使用者很可能重复踩。本节只记录该缺口，本轮不修改 GUI。

以上仍是单一样本对比：不得外推为任何不透明输入都必然如此，也不得外推为任何抠图输入都能通过；事实上，第一个已抠背景样本也没有通过中性保真门。

## 16. 透明背景输入提示缺口关闭（2026-08-08）

§15.4 记录的可用性提示缺口已关闭：GUI 仅在选择模型模式时展示“应使用已抠背景、背景透明的角色图”及其保真统计原因，同时明确该提示不是硬阻断、已抠背景也不保证通过；显式 `model` 命令的 `--source` 帮助和固定操作文档同步同一边界。静态 GUI 回归断言锁定了前提与“不保证通过”的限定。

本节只关闭“下一个使用者看不到输入前提”这一可用性缺口，不改变 alpha 或保真测量逻辑、三项阈值、`neutral_fidelity.status`、motion 告警行为或 candidate 硬门。P1-2 仍未跑通、仍未关闭，状态继续为 `GATE_F_NOT_EVALUATED`；原生 runtime、权重许可和禁止 `.moc3` 的边界均不变。
