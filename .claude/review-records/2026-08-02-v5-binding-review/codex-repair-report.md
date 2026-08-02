已完成 Gate F v5 绑定修复包。严格 fail-closed，未弱化断言，未 commit/push，`GATE_F_NOT_EVALUATED` 与固定成功措辞保持不变。

## F1–F7 实现

| 项目 | 修复 |
|---|---|
| F1 | PSD 投影完成后仅设置模块标志；attestation 改为在 `runpy.run_path()` 完成全部上游收尾后写出。`SystemExit(0/None)` 仍写，非零不写；未执行投影直接拒绝。手动模式语义保留。[entrypoint:311](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_entrypoints/see_through_v3_nf4_source_preserve_v5.py:311) [entrypoint:353](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_entrypoints/see_through_v3_nf4_source_preserve_v5.py:353) [entrypoint:376](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_entrypoints/see_through_v3_nf4_source_preserve_v5.py:376) |
| F2 | worker 在最终 `_inventory` 和固定 URI 校验后，按 `output/input` 相对路径重算 manifest digest；workbench 对留存 `model-output/input` 独立重算。不符分别硬失败或降级为有界 `MODEL_ENTRYPOINT_ATTESTATION_MISMATCH`。[model_worker.py:1204](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_worker.py:1204) [model_workbench.py:1082](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_workbench.py:1082) |
| F3 | `_artifact_manifest` 增加 256 条目上限、累计 `MAX_MODEL_RESULT_BYTES` 预算，并在读取超额文件前拒绝。[model_worker.py:791](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_worker.py:791) |
| F4 | worker 自身断言 attestation `source_sha256` 等于原始输入摘要。[model_worker.py:1173](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_worker.py:1173) |
| F5 | challenge、源路径和 attestation 目标经父环境与 `WSLENV` 透传；源路径也不再作为 `--srcp` 出现在 `wsl.exe` argv，而由 v5 入口进程内注入。[model_worker.py:502](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_worker.py:502) [model_worker.py:533](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_worker.py:533) [entrypoint:380](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_entrypoints/see_through_v3_nf4_source_preserve_v5.py:380) |
| F6 | 新增独立 `LEGACY_V4_*` 字面量；v4 inference/postprocess 从 digest 钉死的归档 profile 独立验证并派生，旧 0.4 报告投影仍使用历史标识。[model_worker.py:40](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_worker.py:40) [model_workbench.py:983](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_workbench.py:983) [model_workbench.py:1487](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_workbench.py:1487) |
| F7 | REQUIRED_FILES 补齐 motion-draft v0.2 两份及 candidate v0.2 三份 schema。[validate_docs.py:66](/Users/cornna/project/OneClick2D/scripts/validate_docs.py:66) |

正向 fixture 现在从真实产物派生摘要；补充了“摘要指向另一集合”“消费后产物变动”“worker 源绑定不符”及资源上限用例。[worker tests:1229](/Users/cornna/project/OneClick2D/tests/test_gate_f_model_worker.py:1229) [workbench tests:766](/Users/cornna/project/OneClick2D/tests/test_gate_f_model_workbench.py:766)

## v5 版本与 digest

按合同就地更新 v5，未升 v6：

- Profile ID：`see-through.v3.nf4.1280.wsl2.source-preserve.v5`
- 新 entrypoint SHA-256：`db470b4b006210d2f20e54850e60a33a176d0c6e7baf3eb4ff9d56992b7e9ca7`
- Profile 声明与实际文件完全一致。[profile:4](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_profiles/see-through-v3-nf4.json:4) [profile:47](/Users/cornna/project/OneClick2D/spikes/gate_f_runner/model_profiles/see-through-v3-nf4.json:47)

## 禁改文件证明

以下 SHA-256 与开工时记录逐项相同：

```text
ae4d26b042b8b15e7bdcfdacd11c50b16d97c1ccf19aad94162dd67046e1642f  see_through_v3_nf4_source_preserve_v4.py
6b625faa99022f6edfa5faba97b23054331b9276501e2b02953cb783f357ec71  see_through_v3_nf4_source_preserve.py
63a192527599ddb567589a6515d7631399df2b11d67c004cf4cc1898000f2a58  see_through_v3_nf4.py
569e0ced8bcc4b144bfc787e0e37f2d90fc263081ceac3c063eabf26ce1c14df  nf4_marigold_device_policy.py
d24de59690e0db2c64828e580eed8b00f939d5327b255ef59f1826f8cf582ae3  see-through-v3-nf4.source-preserve-v4.json
```

## 收尾门

最终代码状态实跑：

```text
Ran 249 tests in 637.902s
FAILED (errors=12, skipped=16)
```

12 个错误全部为约定的 `test_gate_f_gui_server`：

```text
PermissionError: [Errno 1] Operation not permitted
self.socket.bind(("127.0.0.1", 0))
```

即 221 项通过、16 项环境性 skip，仅剩用户将在宿主机复跑的 12 个 GUI socket 测试。另有定向 worker/workbench：

```text
Ran 85 tests in 38.619s
OK (skipped=7)
```

Preflight：

```text
run_id=run.v5fix-preflight status=LOCAL_TECHNICAL_PREFLIGHT_PASS
gate_f=GATE_F_NOT_EVALUATED
bundle=run.v5fix-preflight.bundle/bundle-index.json
```

文档校验：

```text
Initiation/documentation lint passed: 38 Markdown and 44 JSON files checked.
```

`git diff --check` 无输出，通过。

## `git diff --stat`

这是最终工作树相对基线的原始 tracked stat；不包含 untracked 的 v5 entrypoint、归档 v4 profile 和 v0.2 schema：

```text
20 files changed, 1328 insertions(+), 115 deletions(-)
```

完整 stat 中还包含开工前已有的文档、示例、candidate/motion 改动。长测试期间另出现两个新的越界改动：

- `docs/handoff/GATE_F_AUDIT_REMEDIATION_HANDOFF.md`
- `spikes/gate_f_runner/model_profiles/README.md`

本轮未触碰或回退它们，只作报告。本轮实际写入均在合同允许范围内；candidate/motion fixture 无需追加修改。

## 真机 GPU 仍需验证

- WSLENV 在真实 Windows/WSL2 上原样透传三个 attestation 环境变量。
- Process Explorer/`Win32_Process.CommandLine` 中不出现 challenge 或源路径。
- `stats.json` 的 `psd_time_s`/`total_time_s`、`input_depth.psd` 等上游收尾文件写完后，attestation 才产生。
- 正常返回及 `SystemExit(0/None)` 均写 attestation；非零退出不写。
- 真机最终 manifest 与 worker、workbench 两次独立重算完全一致。
- 实际 CUDA 设备/offload disposition 和 PSD 投影 readback 均满足 attestation 声明。

重复 preflight 的旧生成物已可恢复地移至 `/private/tmp/oc2d-v5fix-preflight-archive.ZtXeZR` 和 `/private/tmp/oc2d-v5fix-preflight-final-archive.4UPlXZ`；当前最终 bundle 已重新生成。

Codex session ID: 019fc211-d239-77d2-93ef-fe9fdb9d88c2
Resume in Codex: codex resume 019fc211-d239-77d2-93ef-fe9fdb9d88c2
