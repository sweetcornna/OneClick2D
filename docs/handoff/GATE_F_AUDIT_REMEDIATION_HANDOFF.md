# Gate F 预研审计修复 —— 任务交接

> 交接时间：2026-08-01（UTC）。适用分支：`feat/gate-f-runner`。
> 本文只描述 `spikes/` 下的**可丢弃预研**修复工作。它不是 Gate F 结论、不是 schema/package conformance 证据、不是模型质量或 PSD 互操作证明。所有本地运行状态仍为 `GATE_F_NOT_EVALUATED`。

## 0. 一句话现状

针对 Gate F 预研代码的一轮**安全/契约审计 → 分包修复 → 对抗复核**循环已跑完 5 轮，27 条发现均已落地修复并逐包复验；**唯二未收口的是最后的“完整套件复跑”与“最终确认性对抗复核”**——上一轮会话进程重启把这两个后台结果丢了。全部改动仍在工作树里**未提交**。

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
15. [中] README/CONTRIBUTING/CLAUDE.md 未说明模型命令的宿主 shell，`C:/...` 路径在 WSL 下按相对路径解析并以通用错误失败。→ 三处文档补说明；**C7 收尾**：POSIX 下 `C:/...`、`C:\...` 在工作区创建前即拒绝，退出码 64，有界拒绝码 `WINDOWS_SOURCE_PATH_REQUIRES_WINDOWS_HOST_SHELL`，不回显路径。

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
  （本次交接已独立确认：当轮 profile 中 `entrypoint.sha256` 与 `entrypoint.device_policy.sha256` 与磁盘文件实算 sha256 完全一致，且各只有一个值；后续 active v5 的新摘要链见 §11。）

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

### P1-2 · 端到端真机链路验证

F1 的修复目前只有回归测试覆盖，**没有跑过真实的 GPU 链路**。需要在 Windows 主机 + 隔离 WSL2 worker 上实跑一次：

```powershell
python -m spikes.gate_f_runner model --source "C:/path/to/right-cleared.png" --run-id run.local-model
python -m spikes.gate_f_runner motion --run-id run.local-model
python -m spikes.gate_f_runner model-candidate --run-id run.local-model
python -m spikes.gate_f_runner verify-model-candidate --run-id run.local-model
```

验收标准：`model` 产出可激活运行（不是 `review_required` 降级），链路不在第一步后中断；末两条只写 `LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED` + `GATE_F_NOT_EVALUATED`。素材必须权利明确，且**不得入库**。

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

- **模型支持命令必须从 Windows PowerShell / cmd 运行**，使用锁定的 Windows CPython + Pillow 12.1.0 环境；`model --source` 必须传 Windows 主机路径。**不支持**直接从 WSL shell 调用（这是发现 15 的修复内容，README / CONTRIBUTING / CLAUDE.md 已同步）。
- 本机实测：Windows 侧 `python` = 3.14.4，`PIL` = 12.1.0，可直接跑全套。
- WSL 侧若要跑 Pillow 相关测试，上一轮会话建了 `~/.venvs/oc2d-spike`（`Pillow==12.1.0`）；系统 `python3` 无 Pillow，跑套件会大量跳过——**跳过数正常，失败数才是信号**。
- 固定命令清单以 `CLAUDE.md` / `README.md` / `CONTRIBUTING.md` 为准；不要新增 build/dev 命令，也不要引入框架（技术栈未定，需先关 `docs/OPEN_DECISIONS.md` 并写 ADR）。

## 6. 沿用的工作方式

上一轮跑通、建议继续沿用：

1. **主模型决策 + Codex 执行**：主模型切包、写派单提示词、验收；Codex 只做单包实现。
2. **包与包之间文件集互不相交**，才能并行；有依赖的（如 model_candidate 依赖 motion 侧共享重算接口）必须串行。
3. **派单契约**：`do NOT git commit`；范围严格限定到指定文件；涟漪只报告不越界；每包自带收尾门（跑指定测试）。
4. **主模型独立复验**：不采信 Codex 自报的“全绿”，自己复跑测试 + `git diff --stat` 看改动范围是否越界。
5. **每轮修复后跑对抗复核**，专门找修复引入的新缺陷。前两轮各抓出 5 条和 3 条，说明这一步不是形式主义。
6. 后台长任务（完整套件、Codex 作业）**必须落盘到会话外的位置**——上一轮丢结果就是因为它们只存在于会话进程里。

## 7. 红线（不要越过）

- 这些代码全部在 `spikes/` 下，是 Gate F 前的**可丢弃预研**，任何生产包不得导入。
- 成功状态措辞是固定的：`LOCAL_TECHNICAL_PREFLIGHT_PASS` / `LOCAL_WORKBENCH_COMPLETED` / `LOCAL_MODEL_SPIKE_COMPLETED` / `LOCAL_MODEL_MOTION_DRAFT_COMPLETED` / `LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED`，且一律附 `GATE_F_NOT_EVALUATED`。
- 37 帧 bbox quad/affine 结果只能标 `research_draft`；不得描述为专业绑定、成品模型、mesh-delta、`.oc2d` 或 `.moc3`。
- `model-candidate` / `verify-model-candidate` **不生成也不得声称** ballot、paired outcome、`F-USABLE` 或 20 项 Gate F 结果。
- supporting weight 许可元数据仍不完整：禁止权重再分发、禁止产品使用。
- 不实现、解析、检查、fixture 或逆向 `.moc3`。
- 详细边界见 [CLAUDE.md](../../CLAUDE.md)、[docs/index.md](../index.md)、[docs/FEASIBILITY_SPIKE_PLAN.md](../FEASIBILITY_SPIKE_PLAN.md)。

## 8. 一句话给接手人

先备份 `.claude/workflow-runs/whtdulpa9/journal.jsonl`，再跑 P0-1 完整套件和 P0-2 最终确认性复核；这两项收口后，这一轮审计修复才算真正结束，然后才轮到 P1-3 的提交分包。

## 9. 收口附录（2026-08-02，UTC）

> 本节由接手会话（macOS）追加。复核对象：HEAD `ee953de`；§3 所述“未提交”状态已过时——全部修复已作为 `450344c` 提交并经 PR #2 合并进 `main`，审计 journal 随之入库（P1-3、P2-2 闭环）。

- **P0-1 ✅**：完整 Pillow 套件在 macOS（Python 3.14.6 + Pillow 12.1.0 venv）**211 项 OK（16 项平台性跳过，0 失败 0 错误）**；文档 lint、smoke、preflight 均通过。独立第二次复跑（纯净 `git archive` 副本）同样全绿；无 Pillow 系统解释器下 213 项 OK（123 跳过）。注意：macOS 需将 `TMPDIR` 指向已解析路径（`/var`→`/private/var` 符号链接会被工作区硬化校验拒绝，属平台环境因素，非缺陷）；Pillow 按版本安装，锁定文件的 Windows wheel hash 在本机不适用。
- **P0-2 ✅**：两路独立对抗复核（Codex 与 Opus 5）+ 主模型抽验，一致判定 **F1 / F2 / F3 全部 CONFIRMED 闭环**。同时抓出 **8 条新发现（1 中 7 低）**，含一条与 F2 措辞相关：§2.2 “逐产物流式比对（峰值内存恒定）”与实现不符，实际是**产物粒度顺序处理**（单产物整体读入，受声明总量预算约束）。完整证据与新发现清单见 `.claude/review-records/2026-08-02-final-confirmatory-review/`。
- **P1-1 ✅**：全仓库 64-hex 常量 28 项逐条核对，workbench/motion/candidate 生产代码、schema、examples、tests 中无内嵌旧 entrypoint digest 期望；旧 digest `aedb9e25…` 全库零命中。
- **P1-2 仍开放**：需要 Windows + WSL2 GPU 真机，macOS 无法执行。
- **P2-1**：四个杂散文件在干净检出中不存在；若原 Windows 工作副本仍在，需在彼处清理。
- **下一轮待办**：新发现 N1–N8 的分诊与修复（N1 中危：attestation 摘要未入报告、`postprocess_algorithm` 缺 `.psd-postcorrect.v1` 后缀；N6 涉及 profile_id 是否升版，属决策项）。本轮审计修复循环至此收口。（后续状态见 §10。）

## 10. N 系列修复轮（2026-08-02，UTC，同日追加）

§9 所列新发现已完成一轮“分包修复 → 双路对抗复核 → 收尾修复”，全部改动仍在工作树**未提交**：

- **已闭环**：N1（attestation 摘要经 worker 返回、workbench 独立重验后并入报告；`postprocess_algorithm` 发布带 `.psd-postcorrect.v1` 后缀的实际执行标识；历史 v2/v3 不变且携带 attestation 会被拒）、N2（描述符与可信重算证据的 (sha256, byte_length) 在任何产物 I/O 前精确核对）、N3（`_png_facts` 解码前验画布 + 钉版加载 + 炸弹护栏）、N4（active v4 阈值改读 profile 的 31，附 `alpha_threshold_source`；v2/v3 保持 15 并标注 legacy 来源）、N5（新增 `.gitattributes` 保护全部 raw-digest 绑定路径）、N8（`purpose_created.py` 共享模块，生产者/验证器字节级等价实证）。双路复核确认闭环（记录：`.claude/review-records/2026-08-02-fix-round-adversarial-review/`）。
- **复核抓出的第二批缺陷已收尾修复**：D1/Codex#2（报告 `format_version` 升 0.4.0，loader 对历史 v2/v3 的 0.3.0 持久化报告按投影严格验证，v4 的 0.3.0 与未知版本给专门版本错误，附静态字节 fixture 回归）、Codex#3（attestation 组件设备必须精确等于顶层 `execution_device`，覆盖裸 `cuda`/`cuda:1`/混合设备负例）、D2（reason_codes 列表去别名）、D3（`MAX_IMAGE_PIXELS` 三处改共用进程级锁上下文管理器，含线程交错回归）、D4/D5（死参数与危险默认参数清除）、D6（纵深预算测试改名标注）、D7（本文档 §2.2 F2 措辞更正）、D8（non-active 文案）。
- **当轮显式留决策（后续状态见 §11）**：attestation 与 run/source/产物的绑定（Codex#1，中危——当轮 attestation 是可逐字复现的自述，非执行期密码学证明）与 N6（profile_id 升 v5 还是加 `attestation_revision`）。二者同属 digest 链决策域，已在后续一并定夺并实现。N7 按复核结论接受现状（loopback-only，实测 ~30 ms/帧）。
- **注意**：`format_version` 升 0.4.0 意味着本轮之前发布的 **active v4** workbench 报告需重新生成（历史 v2/v3 报告仍按 0.3.0 投影可验，符合仓库承诺）；当时尚无真实 GPU 运行产物，实际影响为零。v4 后续成为历史 profile，active v5 报告版本升为 0.5.0，见 §11。
- 验收状态：完整套件（Pillow venv）全绿、`preflight` `LOCAL_TECHNICAL_PREFLIGHT_PASS` + `GATE_F_NOT_EVALUATED`、文档 lint 通过（以主模型宿主机独立复跑为准；Codex 沙箱内 `test_gate_f_gui_server` 的 12 项 socket bind 失败为沙箱限制，宿主复跑通过）。P1-2（Windows + WSL2 GPU 真机链路）仍开放。

## 11. v5 digest 链与每次运行清单绑定（2026-08-02，UTC，同日追加）

§10 留下的两个决策已作为同一变更闭环。由于新的运行绑定改变了入口字节、报告语义和下游可消费身份，而仓库内尚无真实 v5 运行产物，本轮选择把 active profile 升为 `see-through.v3.nf4.1280.wsl2.source-preserve.v5`，同时把原 v4 profile 原字节归档为 `model_profiles/see-through-v3-nf4.source-preserve-v4.json`。历史 v2/v3/v4 继续按各自原 profile/入口摘要只读验证，不追溯获得 v5 语义。

- **每次运行绑定**：受信父进程为每次 WSL2 调用生成一次性 challenge；v5 入口在上游脚本完整成功返回（含成功 `SystemExit(0/None)`）且 PSD 像素投影确实执行后，记录源图 SHA-256、最终产物清单及其摘要。非零退出或未完成 PSD 投影不会发布 attestation。
- **三次独立核对**：worker 消费 attestation 时逐项核对 challenge、源图摘要、声明清单和磁盘清单；固定 inventory/PSD 验证后再次从最终发布目录重算清单摘要；workbench 构建或重载报告时再从留存的 `model-output/input` 重算。任一产物在这些边界间变动均 fail-closed，报告保持 `model_used: false` 并使用有界 reason code。
- **资源与进程边界**：清单遍历与哈希有固定的累计字节、条目、目录、节点、深度和相对路径长度上限，父进程与 v5 入口使用同一套规则，attestation 排除文件不计入节点上限；challenge、attestation 路径与源路径通过父环境及 `WSLENV` 透传，不再出现在 `wsl.exe` argv。新增 v0.2 motion/candidate schema 和 profile identity，旧 v0.1 schema 保持不变，并加入立项文档 lint 的必需文件列表。
- **摘要冻结**：active v5 入口 SHA-256 为 `8732db76c4fcf3f4bf7e94f3a206456ffbf9bd78ef773aa66d9b793c6f8f1ac5`（遍历边界修复后就地更新 v5，未升 v6），与 active profile 声明一致；归档 v4 profile SHA-256 保持 `d24de59690e0db2c64828e580eed8b00f939d5327b255ef59f1826f8cf582ae3`，v4 入口保持 `ae4d26b042b8b15e7bdcfdacd11c50b16d97c1ccf19aad94162dd67046e1642f`，device policy 保持 `569e0ced8bcc4b144bfc787e0e37f2d90fc263081ceac3c063eabf26ce1c14df`。
- **能力边界不变**：该绑定证明受信父进程看到的源图、attestation 和发布产物清单彼此一致；它不证明被钉死的 entrypoint 确实执行，不是密码学执行证明或可信执行环境保证。完全控制 WSL2 worker 环境者仍可为自造产物生成自洽清单。所有结果继续是 `review_required` 与 `GATE_F_NOT_EVALUATED`，不证明模型质量、PSD 外部互操作、`.oc2d`、专业绑定或 Gate F 可行性。
- **仍开放的真机门**：P1-2 必须在 Windows + 隔离 WSL2 GPU 上执行 active v5 的 `model → motion → model-candidate → verify-model-candidate` 全链路，确认上游脚本收尾后不再修改产物、环境透传在目标 WSL 版本有效、最终清单三次重算一致，并保留权利明确且不入库的输入。macOS 单元测试、标准库合成编排 smoke 与本地技术预检不能替代该证据。

## 12. 遍历/哈希边界收口与静态 fixture 可移植性发现（2026-08-02，UTC，同日追加）

> 本节由 Linux 接手会话追加（原生 Ubuntu，Python 3.14.4 + Pillow 12.1.0 venv，**非 WSL**）。基线 `93aec48`（PR #4 已合并）。对象仍是 `spikes/` 下 Gate F 前可丢弃预研，全部状态继续 `GATE_F_NOT_EVALUATED`。

### 12.1 §9/§11 留下的两条非阻塞加固候选已闭环

两条都记在 `.claude/review-records/2026-08-02-v5-binding-review/README.md` 的"仍开放"末尾，均为基线既存、非当轮引入。本轮一并收口，**未触碰任何被摘要钉死的文件**——active v5 入口仍为 `8732db76c4fcf3f4bf7e94f3a206456ffbf9bd78ef773aa66d9b793c6f8f1ac5`、active profile 仍为 `e53049e5885419bd9d1d5c70d8b2514226ddcab9c33cdc8750d3f206401e4009`，实算与声明一致。

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

### 12.5 仍开放

- **P1-2 真机链路**：本机是**原生 Ubuntu，不是 WSL**，无 `wsl.exe` / `powershell.exe`（虽有 RTX 5070 Ti），因此**无法**执行 active v5 的 `model → motion → model-candidate → verify-model-candidate`。该门仍必须在 Windows 主机 + 隔离 WSL2 GPU 上完成，仍是 Gate F 前唯一未收口的实测门。
