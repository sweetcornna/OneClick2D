# 2026-08-02 最终确认性对抗复核 · 主模型验收记录

> 对应交接文档 `docs/handoff/GATE_F_AUDIT_REMEDIATION_HANDOFF.md` 的 P0-1 / P0-2 / P1-1 收口。
> 复核对象:HEAD `ee953de`(修复提交 `450344c`,已合并 `main`),工作树在复核期间保持干净。
> 一切结论仅针对 `spikes/` 可丢弃预研;`GATE_F_NOT_EVALUATED` 不变。

## 执行方式

- 主模型(Claude Code 会话)调度,两路**互相独立**的复核并行:
  - `codex-review.md` —— Codex(session `019fc0ee-d6e4-74a1-a580-6d88f335e88c`)。
  - `opus5-review.md` —— Opus 5 只读复核子代理。
- 主模型独立复验:自跑完整测试套件、digest 实算、旧 digest 全库搜查,并抽验 Opus5 的 N1/N2/N4 关键事实。

## P0-1 · 完整 Pillow 套件复跑(macOS,Python 3.14.6 + Pillow 12.1.0 venv)

- **211 项 OK(16 项平台性跳过,0 失败 0 错误)**;文档 lint、smoke、preflight(`LOCAL_TECHNICAL_PREFLIGHT_PASS` + `GATE_F_NOT_EVALUATED`)全部通过。
- Opus5 一路在纯净 `git archive` 副本上独立复跑同套件,同样 211 项 OK;并在无 Pillow 的系统解释器下 213 项 OK(123 跳过),标准库契约成立。
- 首轮曾出现 88 错误/13 失败:根因是 macOS `/var`→`/private/var` 符号链接被工作区硬化校验(`runtime.py` `_regular_directory_info`)拒绝,属**平台环境因素**;将 `TMPDIR` 指向已解析路径后全绿。生产代码与测试断言零改动。**注意:Pillow 锁定文件的 wheel hash 是 Windows py314 专用,本机按版本 `Pillow==12.1.0` 安装(无 hash 校验),与锁定环境存在此一处偏差。**

## P0-2 · F1 / F2 / F3 终审判定

两路独立复核 + 主模型抽验,结论一致:

| 项 | 判定 | 关键证据 |
|---|---|---|
| F1 model CLI 受信源发布 | **CONFIRMED(双路)** | CLI/GUI 共用 `run_normalized_model_workbench()`,`_publish_bytes` 在 worker 调用前(`model_workbench.py:1314→1316`);`_source_trust` 四重绑定;端到端回归 `test_model_cli_publishes_trusted_source_and_reaches_motion_and_candidate` 在 P0-1 全绿中实跑通过 |
| F2 acceptance 内存耗尽面 | **CONFIRMED(双路)** | 声明总量预算在任何产物读取前生效(`acceptance.py:389-393`),37.5 GiB 面 → ~64 MiB;解码严格在逐字节相等之后。两路一致指出:实现是"产物粒度顺序处理"而非交接文档所称"流式/峰值恒定",措辞需修正(见 N2) |
| F3 profile digest 归真 | **CONFIRMED(双路 + 主模型实算)** | 入口 `ae4d26b0…`、device policy `569e0ced…` 与 profile 声明逐一吻合且各单值;`_validated_entrypoint` 单值比较无白名单;旧 digest `aedb9e25…` 全库零命中 |

## P1-1 · F3 涟漪

Opus5 对全仓库 64-hex 常量做了 28 项逐条核对(见 `opus5-review.md` §4):workbench/motion/candidate 的生产代码、schema、examples、tests 中**无任何内嵌旧 entrypoint digest 期望**。主模型 grep 复验零命中。唯二不可磁盘验证的是 `LEGACY_*_PROFILE_SHA256`(v2/v3 历史 profile 内容从未入库,F3 之前即如此,归入 N6)。收口。

## 本轮复核新发现(1 中 7 低,详见 `opus5-review.md` §5;Codex 一路未报新缺陷)

主模型已抽验 N1/N2/N4 的关键事实属实。均为**下一轮待办**,不推翻 F1/F2/F3 闭环判定:

- **N1 [中]** attestation 校验后即弃、报告不含其摘要;`postprocess_algorithm` 发布不带 `.psd-postcorrect.v1` 后缀的旧标识——provenance 归真不彻底。
- N2 [低] F2 预算比可达上界宽 ~424×;可信长度未在读取前用于拒绝;交接文档"流式/恒定"措辞不实。
- N3 [低] `_png_facts` 先解码后验画布,绕过 Pillow 钉版与解压炸弹护栏。
- N4 [低] 可见 alpha 阈值 workbench=15 vs v4/candidate/profile=31,方向 fail-closed 但软边缘素材会被非预期阻断。
- N5 [低] digest 绑定对行尾敏感而仓库无 `.gitattributes`(Windows `autocrlf` 检出会使 model 路径整体不可用)。
- N6 [低] profile 字节与入口语义已变但 `profile_id` 仍为 v4;涉及 CLAUDE.md 固定 profile 名,**属决策项,需项目所有者定夺**(升 v5 或加 `attestation_revision`)。
- N7 [低] GUI 单帧请求触发完整 bundle 验证(~30 ms/帧,可接受,可选优化)。
- N8 [低] 验证器与生产者四处逐字复制,"独立重算"实为"确定性字节可复现校验",有漂移风险。

## 遗留

- **P1-2(Windows + WSL2 GPU 真机链路)本机(macOS)无法执行,仍开放。**
- 交接文档 P2-1 所列杂散文件在本机干净检出中不存在;若 Windows 工作副本仍在,需在彼处清理。
