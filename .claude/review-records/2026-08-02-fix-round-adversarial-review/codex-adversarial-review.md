# 修复轮对抗复核 · Codex 一路(原文存档)

> 复核对象:工作树未提交修复(基线 `ee953de`,N1/N2/N3/N4/N5/N8 修复包 A+B 之后)。2026-08-02 (UTC)。
> Codex session ID: 019fc14a-dbac-7671-bc5b-35e1198c29d7。判定:needs-attention。
> 以下为 Codex adversarial-review 输出原文。

---

Target: working tree diff
Verdict: needs-attention

不应发布:attestation仍可被重放以伪造执行来源,且修复会使受支持的历史v2/v3持久化运行不可读。

Findings:
- [medium] 可重放的自述attestation可伪造v4执行来源 (spikes/gate_f_runner/model_worker.py:529-545)
  `entrypoint_attestation`只校验一组公开固定字段,不包含run ID、source SHA、产物清单/PSD摘要或一次性挑战。因而可直接构造或重放合法摘要,无需执行被钉死的entrypoint。workbench会据此清空attestation reason;只要可变运行目录中的文件描述符、可信源和重建图自洽,就会将`model_used`置为true,并解锁motion/candidate。PSD检查仅验证结构和元数据,无法证明本次运行确实执行了`.psd-postcorrect.v1`。
  Recommendation: 将attestation绑定到父进程生成的每次运行挑战、source SHA及规范化产物清单摘要(至少包含PSD摘要),并由可信父进程验证;同时独立核对PSD图层像素与后处理语义PNG。未绑定的摘要不得解除激活门。
- [medium] 新增报告字段追溯破坏历史v2/v3持久化运行 (spikes/gate_f_runner/model_workbench.py:669-673)
  补丁为所有重算报告新增`alpha_threshold_source`,但报告版本仍为`0.3.0`。旧v2/v3持久化的`workbench-report.json`不含该字段,而loader会把它与当前重算对象做完全相等比较,因此合法历史报告必然被拒绝;直接状态查询失败,GUI列表还会静默隐藏该运行。这违反了仓库明确承诺的v2/v3只读兼容验证。
  Recommendation: 升级报告格式版本,并增加profile/version感知的迁移:先按旧0.3字段投影严格验证历史报告,再返回新版重算报告。加入真实旧形状的v2/v3持久化回归fixture。
- [low] 组件设备可与声明的cuda:0相矛盾仍通过attestation (spikes/gate_f_runner/model_worker.py:582-596)
  顶层`execution_device`被钉为`cuda:0`,但组件校验只要求VAE hook和UNet storage是任意`cuda`/`cuda:*`。实测`cuda:1`组件会与顶层`cuda:0`一起被接受并发布,workbench不会产生attestation mismatch。这会把错误GPU放置、资源占用及潜在多GPU漂移记录为有效的有界执行来源。
  Recommendation: 要求所有VAE execution-hook和UNet storage设备严格等于规范化后的顶层`execution_device`,生产端策略验证也使用同一规则;增加`cuda`、`cuda:1`及混合设备的负向测试。

Next steps:
- 先修复attestation与本次运行/产物的强绑定,并增加重放伪造回归测试。
- 为0.3历史报告实现版本化兼容验证,覆盖持久化v2/v3目录。
- 收紧组件设备一致性断言并增加错误GPU序号测试。
