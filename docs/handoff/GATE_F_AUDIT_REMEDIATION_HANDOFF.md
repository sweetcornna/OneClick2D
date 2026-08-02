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
- **F2** [中] acceptance 验证有 37.5 GiB 内存耗尽面：重算比对前把全部产物读进内存，74 个 PNG 每个可声明 512 MiB 且无总量上限，违反仓库“资源超限严格拒绝”契约。→ 先校验声明长度总量预算，再逐产物**流式**比对（峰值内存恒定），字节相等后才解码。
- **F3** [中] profile 声明过期 entrypoint digest：v4 入口文件被改后，worker 用双常量同时接受新旧 digest，报告发布的仍是旧 digest，provenance 失真。→ profile 记录实际执行文件的 digest、纳入 device-policy 文件、移除双 digest 例外；profile 名保持 `source-preserve.v4` 不变（算法语义未变，只是 attestation 归真）。
  （本次交接已独立确认：profile 中 `entrypoint.sha256` 与 `entrypoint.device_policy.sha256` 与磁盘文件实算 sha256 完全一致，且各只有一个值。）

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
