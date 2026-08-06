# ADR-0002：宿主中立模型身份与原生 Linux 运行时

- **状态**：Proposed
- **日期**：2026-08-04
- **Owner / Deciders**：TBD；必须由 Gate 0 具名决定
- **相关记录**：[D-005](../OPEN_DECISIONS.md)；[Gate 0](../gate-records/GATE_0.md)；只有不可变 Gate 记录绑定本 ADR 版本并具名批准后，状态才能改为 `Accepted`

## 背景

Gate F 前的 `spikes/gate_f_runner` 模型 worker 是可丢弃预研，不是生产运行时。按 v5 profile 的描述无法在任何宿主上搭出可运行环境：profile 缺少 `sys.path` 前提，依赖清单不自足，scheduler 走硬编码 Hugging Face 缓存，离线解析还要求 `refs/main`；同时 v5 存在功能缺陷，因此从未端到端跑通。可用于真机验证的设备是原生 Ubuntu 与 RTX 5070 Ti 12 GB，不是 WSL，也没有 WSL 路径可供该设备使用。

需要决定的是：active profile 身份是否继续绑定 WSL2，还是让 profile ID 与宿主解耦、由运行时字段如实记录当前宿主和隔离事实。若不决定，文档会继续把不可运行且未经真机证明的 WSL2 身份描述为 active，并把原生无隔离运行误称为隔离 worker。

本决定不选择生产技术栈；[D-005](../OPEN_DECISIONS.md) 仍为 Open。它不改变 `.moc3` 边界，也不改变 Gate F 结论：全部结果仍为 `GATE_F_NOT_EVALUATED`。

## 决定驱动因素

- active 身份必须与实际可运行、可验证的宿主事实一致，固定产物集合必须达到 55/55、零缺失、零多余。
- profile ID 不应把可变宿主类型编码为能力声明；宿主、隔离和加载约束必须由机器可校验字段表达。
- 历史 v2、v3、v4、v5 WSL2 profile 及其原摘要必须保持只读兼容，不追溯获得 v6 语义或 v6 的运行/产物清单绑定。
- 原生运行时没有隔离边界，文档和校验必须使用同一限制性声明，不能暗示安全边界。
- supporting weight 许可元数据仍不完整；权重禁止再分发、禁止入库、禁止产品使用。
- 变更仅服务 `spikes/` 下可回滚、可删除的 Gate F 前预研，不能成为生产包依赖。

## 考虑过的选项

### 选项 A：维持 WSL2-only 身份

继续使用 `see-through.v3.nf4.1280.wsl2.source-preserve.v5` 作为 active 身份。优点是无需改变对外名称；缺点是可用真机没有 WSL，且 v5 profile 描述本身不足以搭出可运行环境、从未端到端跑通。该选项会让身份、实测宿主和真实安全边界继续冲突，拒绝。

### 选项 B：宿主中立 ID，由运行时字段记录宿主事实

使用 `see-through.v3.nf4.1280.source-preserve.v6`，由 `runtime.kind`、`runtime.isolation` 和 `runtime.isolation_notice` 记录并校验当前运行事实。优点是身份不再谎称 WSL2，当前原生 Linux 支持范围和无隔离边界可以被机器拒绝式校验，历史 WSL2 profile 仍可保留原身份；代价是需要升级 profile、入口、设备策略、依赖清单和消费方契约。选择此项。

### 选项 C：新建独立 native profile ID，与 WSL2 profile 并列

新增带 `native` 的 active ID，同时继续把 WSL2 v5 作为并列 active 路径。优点是宿主一眼可见；缺点是把部署事实继续固化进身份，并暗示两条均受支持、语义等价的 active 路径，而 WSL2 v5 没有可运行或端到端证据。该选项还会扩大一次性预研的维护面，拒绝。

## 决定

选择选项 B。active profile 为宿主中立 ID `see-through.v3.nf4.1280.source-preserve.v6`；当前确切支持范围仅为原生 Linux：`runtime.kind = native-linux`、`runtime.isolation = none-host-local`、`runtime.isolation_notice = "无隔离边界、仅限本机"`。worker 必须同时逐字校验后两个值，任一不符即拒绝加载；native profile 不得包含 `distribution` 字段。该支持范围不包含 Windows、macOS 或 WSL2，也不承诺生产部署。

v6 固定入口为 `see_through_v3_nf4_source_preserve_v6.py`，设备策略为 `nf4_marigold_device_policy_v6.py`，`policy_id = see-through.v6.nf4-marigold-bounded-offload.v2`。依赖清单为 `requirements-see-through-v3-nf4.txt`，`dependencies_sha256` 以 `b14584b1` 开头，相对旧清单新增 `pycocotools==2.0.11`。固定解释器/计算栈为 Python 3.12.13、torch 2.8.0+cu128、CUDA 12.8。`runtime.python_path_entries = ["common"]`，由受信探针实算 realpath 验证其生效。

v6 保留 v5 的语义：不高于 31/255 的 alpha 清零并对保留区间线性重映射；按逐层深度把原图 RGB 回填到最前可见语义层，隐藏层保留模型生成像素；按清理后各层最大 alpha 重建中性图；以每次运行的一次性 challenge、源图 SHA-256 和产物清单摘要绑定本次运行与本次产物清单，并由受信父进程独立重算后校验。

**已证明的证据**：指定原生 Ubuntu/RTX 5070 Ti 12 GB 真机运行退出码为 0，产物集合 55/55；设备、PSD 投影和 13 个语义层的验证值符合第“发布与验证”节记录。worker 与 model 命令均产生规定的成功状态并继续附带 `GATE_F_NOT_EVALUATED`。

**仍属假设**：同一 profile 在其他原生 Linux 主机上可复现；宿主中立 ID 足以容纳未来经独立决定支持的其他运行时；模型的语义拆层、隐藏区域质量、动态链路和产品适用性均未由本决定证明。模型报告必须保持 `review_required`。

## 影响

### 正面影响

- active 身份、运行时字段和真机证据一致，不再把 WSL2 当作未经验证的当前宿主事实。
- worker 对无隔离声明、禁止 `distribution` 和受信 `python_path_entries` 探针执行拒绝式校验，配置漂移更容易被发现。
- v2、v3、v4、v5 WSL2 profile 保留原字节、原摘要和只读验证边界，迁移不改写历史证据。

### 负面影响 / 接受的权衡

- native 运行时没有隔离边界、仅限本机；相对 WSL2 路径，这是明确的能力倒退。该代价被接受，因为此运行时只服务 `spikes/` 下 Gate F 前可丢弃预研，不是生产运行时，也不得被生产包导入。
- 宿主中立 ID 不表示跨宿主支持；任何读者都必须同时检查 `runtime.kind` 与隔离字段。
- 新 profile、入口、设备策略和依赖摘要增加一组短期维护对象；回滚方式是停用 v6 并删除该可丢弃路径，而不是把 v5 重新描述成已验证 active 路径。

### 隐私、安全、法律与许可

- 原生 profile 不提供任何隔离或安全边界，只允许权利明确的本机输入；不得扩成外网或生产服务。
- 运行绑定只证明受信父进程看到的 challenge、源图摘要与发布清单彼此一致。它不证明被钉死的 entrypoint 确实执行过，不是密码学执行证明，也不是可信执行环境保证；完全控制 worker 运行环境者仍可为自造产物计算自洽清单。
- supporting weight 许可元数据仍不完整，权重禁止再分发、禁止入库、禁止产品使用；本决定不构成模型、数据或产品许可批准。

### 可复现性与资源

- profile 固定入口、设备策略、依赖清单和摘要，并固定 Python 3.12.13、torch 2.8.0+cu128、CUDA 12.8。
- 当前资源证据仅覆盖原生 Ubuntu、RTX 5070 Ti 12 GB：耗时 406 s，峰值显存 6.29 GB / 12 GB。该单次结果不是其他硬件的性能承诺。
- `common` 路径由受信探针基于 realpath 验证；离线依赖与模型解析仍必须服从 profile 的固定摘要和本机资源边界。

## 发布与验证

- 将 active profile 切换为 `see-through.v3.nf4.1280.source-preserve.v6`，并同步默认文档；v5 原字节归档为 `spikes/gate_f_runner/model_profiles/see-through-v3-nf4.source-preserve-v5.json`，旧 `requirements-see-through-v3-nf4-wsl2.txt` 仅供历史验证。
- v2、v3、v4、v5 继续按各自原 profile、入口和摘要只读验证；历史 `wsl2` 代码路径的逐字节等价由回归测试证明，不获得 v6 语义或 v6 的运行/产物清单绑定。
- 原生 Ubuntu + RTX 5070 Ti 12 GB 真机结果：退出码 0；耗时 406 s；峰值显存 6.29 GB / 12 GB；产物 55/55 与 `_expected_output_uris()` 固定集合完全一致，0 缺失、0 多余；`execution_device = cuda:0`；`psd_projection_verified = true`；语义层 13/13 非空。
- worker 结果为 `WORKER_NATIVE_OK`；model 命令成功写入 `LOCAL_MODEL_SPIKE_COMPLETED` 与 `GATE_F_NOT_EVALUATED`。这不生成 Gate F 结论，不证明模型质量或后续链路。
- 文档 lint 必须解析本 ADR 的本地链接并覆盖索引；回归测试必须拒绝错误 isolation 值、错误 notice、native profile 的 `distribution` 字段，以及未由受信 realpath 探针确认的 `common` 路径。
- 若需回滚，停止发布 v6 新运行并保留既有 v6 证据只读；不得修改历史 profile 字节或把 v5 提升为已验证 active 路径。

## 重新评审触发

出现以下任一可观测条件即重新评审：D-005 被正式关闭；任何生产包拟导入该运行时；拟支持 WSL2、Windows、macOS 或第二种 `runtime.kind`；拟增加隔离或安全边界声明；profile 的 Python/torch/CUDA、入口、设备策略、依赖摘要或 12 GB 资源上限发生变化；或任一次受控原生 Linux 复现未达到退出码 0、固定产物 55/55、`psd_projection_verified = true`、语义层 13/13 非空。
