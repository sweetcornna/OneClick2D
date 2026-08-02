# 最终确认性对抗复核 · Codex 一路(原文存档)

> 复核对象:HEAD `ee953de`(修复提交 `450344c`),2026-08-02 (UTC)。
> Codex session ID: 019fc0ee-d6e4-74a1-a580-6d88f335e88c(`codex resume` 可续)。
> 以下为 Codex 输出原文。

---

结论:当前 `HEAD ee953de` 中的目标文件与修复提交 `450344c` 一致,工作树干净。F1/F2/F3 均已闭环;未发现本轮修复引入的可报告新缺陷。

### F1 — CONFIRMED

- CLI `model` 已调用公共入口 `run_normalized_model_workbench()`,并传入真实 worker:`spikes/gate_f_runner/__main__.py:140`。
- 公共入口先生成并原子发布 `trusted-model-source.png`,随后才调用 worker:`model_workbench.py:1314`、`model_workbench.py:1316`。
- GUI 包装器也复用同一入口,而非维护另一条生成逻辑:`model_workbench.py:1347`。
- 发布后并非只检查"文件存在":校验同时绑定 worker 的 `source_sha256`、规范化输入重建像素和 worker 输出的 `src_img.png`:`model_workbench.py:515`、`:520`、`:528`。
- 后续门正确消费该状态:`motion` 重载 workbench 并要求 active v4 的 `neutral_fidelity.status == pass`;`model-candidate` 再要求同一状态并重算 motion:`model_motion_draft.py:971`、`model_candidate.py:861`。
- 回归覆盖实际 `model` CLI 入口,再生成 37 帧 motion 和 candidate:`tests/test_gate_f_model_workbench.py:441`。

因此,原来的"第一步成功但缺少受信源,第二步必然拒绝"路径已不存在。真实 Windows/WSL2 GPU 执行仍属于交接文档 P1-2,不是本次只读代码复核的运行时证明范围。

### F2 — CONFIRMED

- inventory 被封闭为精确的 80 个产物,其中恰好 74 个 PNG:`acceptance.py:32`。
- 每个声明长度要求为严格整数、非布尔且不超过 512 MiB;重复名称被拒绝:`acceptance.py:362`。
- 描述符集合必须与固定 inventory 完全相等,无法靠缺项、别名或重复项规避总量计算:`acceptance.py:380`。
- 在读取任何非 index 的不可信产物前,声明总量被限制为两个 arm 的固定 32 MiB producer 预算,加固定非 arm 证据大小:`acceptance.py:383`、`:392`。74 个 512 MiB 声明不能进入产物读取阶段。
- `_read_matching_bundle_artifact()` 每次只保留一个不可信产物,立即核对长度、摘要和可信重算字节:`acceptance.py:407`。
- PNG 和 PSD 都在字节完全相等后才解码/解析:`acceptance.py:423`、`:626`、`:629`。
- 负向测试覆盖总预算、单产物预算以及"不匹配字节不得到达解码器":`tests/test_gate_f_acceptance.py:92`、`:239`。

准确地说,这里是"产物粒度顺序处理",不是块级文件流比较;但单产物读取也受总预算约束,因此峰值不再随 74 个攻击性声明线性累加,原耗尽面确实被消除。

### F3 — CONFIRMED

- 磁盘实算结果:
  - v4 entrypoint:`ae4d26b042b8b15e7bdcfdacd11c50b16d97c1ccf19aad94162dd67046e1642f`
  - device policy:`569e0ced8bcc4b144bfc787e0e37f2d90fc263081ceac3c063eabf26ce1c14df`

  两者分别与 profile 完全一致:`spikes/gate_f_runner/model_profiles/see-through-v3-nf4.json:45`。

- worker 要求 entrypoint 对象恰好包含单一 `sha256` 和 device-policy 描述符,并分别读取磁盘实算验证:`model_worker.py:142`、`:165`、`:168`、`:181`。
- 实际 WSL 命令使用的正是 `_validated_entrypoint()` 返回文件:`model_worker.py:447`。
- workbench 在发布 identity 前再次验证 profile/entrypoint,并从该已验证 profile 发布实际 digest:`model_workbench.py:878`、`:892`。
- 旧 v4 digest `aedb9e25…c909c31` 在当前 `HEAD` 无任何接受引用;内存替换为该值后,`_validated_entrypoint()` 实测返回 `model entrypoint digest mismatch`。
- 现存 legacy digest 常量只服务于不同的 v2/v3 profile ID,不是同一 v4 profile 的双 digest 例外:`model_workbench.py:827`。
- 下游 candidate 传播当前 `profile_sha256`,没有内嵌旧 entrypoint digest:`model_candidate.py:552`。

### 新缺陷列表

未发现满足报告门槛的新缺陷,因此没有 `severity / file / line / issue / evidence / suggested fix` 条目。

只读约束已遵守:未修改文件、未提交;最终 `git status` 仍为干净的 `main...origin/main`。
