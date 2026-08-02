# 2026-08-02 v5 digest 链与运行清单绑定 · 主模型验收记录

> 基线 `260083c`；对象为 Gate F 前 `spikes/` 可丢弃预研。所有成功状态继续附 `GATE_F_NOT_EVALUATED`，本记录不是 schema/package conformance、模型质量、PSD 外部互操作或 Gate F 可行性证据。

## 决策

§10 留下的两个 digest 链决策一并关闭：

1. active profile 升为 `see-through.v3.nf4.1280.wsl2.source-preserve.v5`；原 v4 profile 原字节归档，历史 v2/v3/v4 只按各自原摘要验证。
2. v5 以每次运行的一次性 challenge、源图 SHA-256 和最终产物清单摘要约束 attestation；受信父进程在消费、最终发布与 workbench 重载边界独立重算。

该绑定不证明被钉死的 entrypoint 确实执行，不是密码学执行证明或可信执行环境保证。完全控制 WSL2 worker 环境者仍可为自造产物生成自洽清单。

## 实现验收

| 项 | 判定 | 证据 |
|---|---|---|
| attestation 快照时序 | CONFIRMED | v5 入口仅在上游脚本完整返回或成功 `SystemExit(0/None)` 后写出；非零退出不写；未完成 PSD 投影拒绝写出。 |
| run/source/artifact binding | CONFIRMED | worker 核对一次性 challenge、原始 source digest、声明清单与磁盘清单；固定 inventory 后再次重算发布清单；workbench 从留存目录再次重算，变异测试 fail-closed。 |
| 资源边界 | CONFIRMED | 清单遍历与哈希有累计字节、条目(256)、目录(64)、节点(320)、深度(8)和相对路径长度(512B)上限,worker 与 v5 入口同规则,attestation 排除文件不计入节点上限;全部在读取任何文件内容前 fail-closed。终轮 A 路对抗复核构造深链/目录循环/并发变异/边界值不对称/异常路径逃逸均被驳回;两侧共 11 项边界回归测试(含遍历期拒绝、AST 提取同规则、legacy v4 独立于 active policy)全绿。 |
| argv 暴露 | CONFIRMED | challenge、源路径和 attestation 路径通过父环境与 `WSLENV` 透传，不出现在 `wsl.exe` argv。 |
| 历史 v4 冻结 | CONFIRMED | 归档 v4 profile 与基线 `260083c` active profile 原始字节相同；v4/v3/v2 入口和 device policy 与基线逐字节相同；v4 identity 从归档 profile 和独立 `LEGACY_V4_*` 常量验证。 |
| v0.2 ripple | CONFIRMED | motion v14、candidate source-preserve-v5、producer/example/schema canonical config digest 对齐；五个 v0.2 schema 纳入立项文档 lint 必需列表；v0.1 schema 与基线零差异。 |

## 冻结摘要

- active v5 entrypoint: `8732db76c4fcf3f4bf7e94f3a206456ffbf9bd78ef773aa66d9b793c6f8f1ac5`（遍历边界修复后就地更新 v5;与 active profile 声明一致。修复前记录为 `db470b4b006210d2f20e54850e60a33a176d0c6e7baf3eb4ff9d56992b7e9ca7`,该字节版本从未产生真机运行产物,无追溯验证需求）
- active v5 profile: `e53049e5885419bd9d1d5c70d8b2514226ddcab9c33cdc8750d3f206401e4009`（仅 entrypoint 声明摘要变更;修复前为 `98578ec3a4e24dc7f2eba0578770c212ce7dd7969de8eedbd54880a63d2a6378`）
- archived v4 profile: `d24de59690e0db2c64828e580eed8b00f939d5327b255ef59f1826f8cf582ae3`
- v4 entrypoint: `ae4d26b042b8b15e7bdcfdacd11c50b16d97c1ccf19aad94162dd67046e1642f`
- device policy: `569e0ced8bcc4b144bfc787e0e37f2d90fc263081ceac3c063eabf26ce1c14df`
- motion canonical config: `b9fea23f0f78cad83a5a87ae453ef957107bb065cf482cde85e531781d0e1db9`
- candidate canonical config: `feaff775a888e85d1f95b0f09c4162f118f02cf084e73837e0f0ab6c4dc92b4c`

## 主模型宿主验证

- 立项文档 lint：通过，38 Markdown / 44 JSON。
- 标准库合成编排 smoke：`status=succeeded`。
- 本地技术预检（Pillow 12.1.0 venv）：`LOCAL_TECHNICAL_PREFLIGHT_PASS` + `GATE_F_NOT_EVALUATED`。
- 完整 Pillow 单元测试（macOS,Python 3.14.6 + Pillow 12.1.0 venv,`TMPDIR` 指向已解析路径）：`Ran 258 tests in 719.797s, OK (skipped=16)`——含 codex 沙箱无法运行的 12 个 GUI socket 测试;16 项 skip 均为环境性(Windows/WSL 专属)。最终文件状态另以同一 venv 定向复跑 worker+workbench 模块：`Ran 95 tests in 38.921s, OK (skipped=7)`。注意:Pillow 锁定 wheel hash 为 Windows py314 专用,本机按版本号安装,与锁定环境存在此一处已知偏差(沿用 2026-08-02-final-confirmatory-review 记录的做法)。
- 两路终轮只读复核：完成。A 路(对抗性,绑定链/遍历边界)全部疑点 REFUTED,资源边界项 CONFIRMED;B 路(冻结/涟漪一致性)五项焦点全部一致,无阻塞发现。次要观察(entrypoint 侧 entry/byte 上限缺行为级用例)已当轮补测并通过。

## 遍历边界追加轮(终轮前)

冻结摘要在 codex 修复包(`db470b4b...`)之后又经历一轮清单遍历资源边界修改:worker/v5 入口新增目录/节点/深度/路径长度上限并统一排除语义(attestation 排除文件不计入节点上限,消除"入口接受、父进程多算 1 后拒绝"的边界不对称),workbench 经共享 `_artifact_manifest` 同步。v5 按合同就地更新未升 v6,新入口摘要 `8732db76...` 已同步至 active profile、handoff 文档与本记录;历史 v2/v3/v4 入口、device policy、归档 v4 profile 与基线逐字节相同(B 路以 `git show 260083c` 逐字节核验)。

## 仍开放

Windows + 隔离 WSL2 GPU 真机链路仍必须执行 active v5 的 `model → motion → model-candidate → verify-model-candidate`，确认目标 WSL 版本的环境透传、上游收尾后的产物稳定性和三次清单重算。输入必须权利明确且不得入库。macOS 单元测试、标准库合成编排 smoke 与本地技术预检不能替代该证据。

终轮 A 路另记录两条预存(基线已存在、非本轮引入)的非阻塞观察,作为后续加固候选:

- `model_workbench.py` `_indexed_files` 全树遍历无节点/深度上限(靠首个非常规节点即拒与事后集合比对兜底);可与清单遍历边界统一。
- worker 清单重算按整文件读入内存,单文件峰值可达 512MiB(设计常量内,有界)。
