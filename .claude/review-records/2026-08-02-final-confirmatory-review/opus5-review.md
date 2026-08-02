# 最终确认性对抗复核 · Opus5 一路(原文存档)

> 复核对象:HEAD `ee953de`(修复提交 `450344c`),2026-08-02 (UTC)。
> 只读复核;工作树未被触碰。以下为 Opus5 复核员输出原文(通知转义已还原)。

---

# 最终确认性对抗复核 —— HEAD `ee953de` (修复提交 `450344c`)

## 0. 复核基线与实测门(附带取得,非任务要求但作为证据)

在 scratchpad 的 `git archive HEAD` 纯净副本 + Pillow 12.1.0 venv 下实跑(macOS 需 `TMPDIR` 指向已解析路径,否则 `require_regular_workspace_root` 因 `/var`→`/private/var` symlink 大面积拒绝——**这是平台假象,不是缺陷**):

| 门 | 结果 |
|---|---|
| `python -m unittest discover -s tests` (Pillow 12.1.0) | **Ran 211, OK (skipped=16)** |
| 同上(无 Pillow,系统 python3.14.6) | **Ran 213, OK (skipped=123)** — 标准库契约成立 |
| `scripts/validate_docs.py` | 通过(38 Markdown / 39 JSON) |
| `smoke --run-id run.stdlib-smoke`(无 Pillow) | `status=succeeded` |
| `preflight` | `LOCAL_TECHNICAL_PREFLIGHT_PASS` + `GATE_F_NOT_EVALUATED` |

preflight 通过是 F2 的关键旁证:它证明 `local_preflight` 产出的 bundle 与 `acceptance` 侧独立重算的 purpose-created 证据**逐字节相等**。

---

## 1. F1 [高] model CLI 受信源发布 —— **CONFIRMED**

**完整调用链已逐跳核对:**

1. `spikes/gate_f_runner/__main__.py:114-156` `_model()`:`:122-123` 在函数体内延迟 import `run_normalized_model_workbench` 与 `run_model_worker`;`:140-147` 调用 `run_normalized_model_workbench(workspace_root, run_id, source_bytes, media_type, run_model_worker, timeout_seconds=...)`。CLI 不再自己拼装 worker 调用。
2. `spikes/gate_f_runner/model_workbench.py:1274-1337` `run_normalized_model_workbench()`:
   - `:1301` `_normalize_upload(...)` 建 run_dir 并产出 normalized 证据;
   - `:1309` `trusted_source_path = run_dir / TRUSTED_MODEL_SOURCE_NAME`;
   - `:1314` **`_publish_bytes(trusted_source_path, _canonical_source_png_bytes(normalized_path))`**;
   - `:1316` 才 `result = model_worker(trusted_source_path, output, ...)`。
   顺序是硬的:发布语句在 `try` 块第一行,worker 调用在其后一行,两者之间没有分支。
3. `:1340-1355` `run_uploaded_model_workbench()`(GUI 路径)纯委托给同一函数 —— GUI/CLI **确实共用**,不存在两条实现。

**受信源如何解锁下游(不是空文件充数):** `_source_trust()` (`:485-550`) 对该文件做四重绑定,任一不成立即写入有界 reason code:`:505-510` 缺失→`MODEL_TRUSTED_SOURCE_EVIDENCE_MISSING`;`:515-516` 与 `result["source_sha256"]` 不符→`..._IDENTITY_MISMATCH`;`:517-527` 从受检 normalized 输入**重新构造**规范画布并与受信源逐像素比对→`..._CANONICAL_MISMATCH`;`:528-530` 与模型自产 `src_img.png` 逐像素比对→`MODEL_SOURCE_REFERENCE_RGBA_MISMATCH`。`:1157` `model_used = source_trust["status"] == "pass"`;`:1133-1134` source_trust 不 pass 时强制把 `neutral_fidelity.status` 降为 `review_required`。

**下游不再中断:** `model_motion_draft.py:974-981` 与 `model_candidate.py:864-865` 都以 `quality.neutral_fidelity.status == "pass"` 且 `identity.profile_id == v4` 为准入。CLI 现在能产出 pass 态,链路打通。

**回归测试是真链路,不是伪造 fixture:** `tests/test_gate_f_model_workbench.py:441-498` `test_model_cli_publishes_trusted_source_and_reaches_motion_and_candidate`:
- `:478-482` 通过 `mock.patch("sys.argv", argv)` 驱动**真实 `main()`** 走 `model` 子命令;
- 唯一被 stub 的是 `spikes.gate_f_runner.model_worker.run_model_worker`(隔离 WSL2/GPU 桥,本地不可跑,是正确的 stub 边界);normalization、受信源发布、`_source_trust`、`_neutral_fidelity`、报告构建全部真实执行;
- `:454-455` 在 stub 内部断言 worker 收到的 `source` **就是** `run_dir/trusted-model-source.png` 且是真实文件 —— 直接证明"worker 调用前已发布";
- `:485-491` 断言 `model_used=True`、`source_trust.status=="pass"`、`reason_codes==[]`、`source_sha256 == trusted_source.sha256`;
- `:494-498` 继续真实调用 `generate_model_motion_draft`(37 帧)与 `generate_model_candidate_preflight`,断言 `LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED` + `GATE_F_NOT_EVALUATED`。

配套负向用例 `:306-329`(删掉受信源后无法激活)与 `:500-531`(worker 改写 src_img 被抓)也在。**判定 CONFIRMED。**

---

## 2. F2 [中] acceptance 验证内存耗尽面 —— **CONFIRMED(但交接文档的"流式/峰值恒定"措辞与实现不符,见新缺陷 N2)**

**总量预算真实存在且在任何产物读取之前生效:** `spikes/gate_f_runner/acceptance.py:341-394` `_read_verified_bundle_index()`:
- `:344-346` 先做目录清单精确匹配(78 个必需名 + index);
- `:348` 只读 `bundle-index.json`(`read_bounded_file` 默认上限 `MAX_JSON_BYTES=1 MiB`);
- `:363-381` 逐条校验 name/digest/byte_length,单条 `1 <= byte_length <= MAX_BUNDLE_ARTIFACT_BYTES`(512 MiB);
- `:389-393` **`producer_byte_budget = 2 * _MAX_ARM_BUNDLE_OUTPUT_BYTES + Σ(非 arm 可信证据长度)`,`sum(声明长度) > 预算` 即 `raise StageContractError("bundle aggregate byte budget exceeded")`**。
- 该函数返回后才发生任何产物读取(`_verify_bundle:589` 调用它,`:591` 起才开始读)。原 37.5 GiB 面 → 现约 **64 MiB**(`2 × 33,554,432` + ~5 KB)。

**解码确实在字节相等之后:** `_read_matching_bundle_artifact` (`:407-425`) 顺序是 `read → 长度比对 (:419) → sha256 比对 (:421) → 与 purpose-created 可信证据逐字节比对 (:423)`,全部通过才 `return data`。所有解码器都在其后:`_load_json_bytes` (`:593`)、`_verify_frame_png` (`:627`)、`parse_layered_psd` (`:635`)。**攻击者控制的字节永远到不了 PNG/PSD/JSON 解码器**(唯一例外是必须先解析的 `bundle-index.json`,它被 1 MiB + `strict_load_json_bytes` 的节点/重复键/NaN 限制约束)。`_verify_frame_png:443` 还把 `DecompressionBombWarning` 升级为 error。

**声明长度 vs 实际长度不一致的行为已核对:** `:416` `read_bounded_file(path, min(maximum, expected_length))`;`runtime.py:52-53` 超限抛 `SpecValidationError`(`contracts.py:34` 是 `ValueError` 子类)→ 被 `:417` 捕获转 `"bundle artifact is unavailable"`;实际文件更小则 `:419-420` `"bundle artifact size mismatch"`。两侧都封闭。

**预算数值与仓库契约一致:** `_MAX_ARM_BUNDLE_OUTPUT_BYTES = 33_554_432` (`:29`) 与真实生产者 `local_preflight.py:42` 的 `max_output_bytes: 33554432` 完全一致;`runner.py:91` 强制 `max_output_bytes ≤ 10 GiB`。预算是从生产者自己的 stage 资源上限推导的,不是拍脑袋常量。

**未发现绕过面。** 判定 **CONFIRMED**(残留的宽松度问题另列为 N2,不影响闭环判定)。

---

## 3. F3 [中] profile entrypoint digest 归真 —— **CONFIRMED**

**磁盘实算 vs profile 声明(复核员自算):**

| 文件 | 磁盘 sha256 | profile 声明 | 位置 |
|---|---|---|---|
| `model_entrypoints/see_through_v3_nf4_source_preserve_v4.py` | `ae4d26b042b8b15e7bdcfdacd11c50b16d97c1ccf19aad94162dd67046e1642f` | 同 | `see-through-v3-nf4.json:47` ✅ |
| `model_entrypoints/nf4_marigold_device_policy.py` | `569e0ced8bcc4b144bfc787e0e37f2d90fc263081ceac3c063eabf26ce1c14df` | 同 | `see-through-v3-nf4.json:51` ✅ |

**双 digest 例外确已移除:** `model_worker.py:142-183` `_validated_entrypoint()`:`:144-149` 要求 entrypoint 键集恰为 `{path, sha256, upstream_script, device_policy}`;`:165-166` `sha256_bytes(exact) != expected_digest → raise`——**单值比较,无 or/白名单**;`:168-182` 对 device-policy 做同构的单值校验(`:174` 还钉死文件名必须等于 `DEVICE_POLICY_PATH.name`)。

**旧 digest 全仓库不存在:** 对 `aedb9e25...`(450344c 之前的 v4 入口 digest)在整个工作树(排除 `.git`)grep,**零命中**。git diff 确认它在 `450344c` 中被 `ae4d26b0...` 替换。

**workbench 侧也走同一校验:** `model_workbench.py:892` 在 active v4 分支调用 `_validated_entrypoint(profile)`,`:898` 才发布 `entrypoint.get("sha256")` —— 发布的是刚刚与磁盘核对过的值,不是自述值。

**残留的 `LEGACY_*_ENTRYPOINT_SHA256` 不是 v4 的双 digest:** `model_worker.py:27` `63a19252...` 对应 `see_through_v3_nf4.py`(磁盘实算一致 ✅),`:32` `6b625faa...` 对应 `see_through_v3_nf4_source_preserve.py`(磁盘实算一致 ✅)。它们只在 `model_workbench.py:828-876` 的历史 v2/v3 profile_id 分支使用,且 `:833`/`:858` 也会实算磁盘文件核对。这正是 CLAUDE.md 要求的"历史结果按各自原 profile/入口摘要验证"。**判定 CONFIRMED。**

---

## 4. P1-1 · F3 涟漪:全仓库 digest 常量清单与核对

对 `spikes/ tests/ schemas/ examples/ scripts/ registries/ docs/` 全量 grep 64-hex 常量,逐条核对:

| # | 常量位置 | 对应文件/对象 | 核对方式 | 结果 |
|---|---|---|---|---|
| 1 | `model_profiles/see-through-v3-nf4.json:47` | v4 入口 .py | 磁盘 sha256 | ✅ 一致 |
| 2 | `see-through-v3-nf4.json:51` | device policy .py | 磁盘 sha256 | ✅ 一致 |
| 3 | `see-through-v3-nf4.json` `dependencies_sha256` | `requirements-see-through-v3-nf4-wsl2.txt` | 磁盘 sha256 = `dac624bb…` | ✅ 一致 |
| 4 | `model_worker.py:27` `LEGACY_ENTRYPOINT_SHA256` | `see_through_v3_nf4.py` | 磁盘 sha256 | ✅ 一致 |
| 5 | `model_worker.py:32` `LEGACY_SOURCE_PRESERVE_ENTRYPOINT_SHA256` | `see_through_v3_nf4_source_preserve.py` | 磁盘 sha256 | ✅ 一致 |
| 6 | `model_worker.py:28` `LEGACY_DEPENDENCIES_SHA256` | 同 #3 文件 | 磁盘 sha256 | ✅ 一致 |
| 7 | `model_worker.py:26` `LEGACY_PROFILE_SHA256` | 历史 v2 profile JSON **内容** | git 全历史只有两版 profile(`03c2eba5`, `d24de596`),**均不等于此值** | ⚠️ 见 N6 说明(不可磁盘验证,非 F3 引入) |
| 8 | `model_worker.py:31` `LEGACY_SOURCE_PRESERVE_PROFILE_SHA256` | 历史 v3 profile JSON 内容 | 同上 | ⚠️ 同上 |
| 9 | `candidate_baseline.py:45` `FROZEN_CANDIDATE_CONFIG_SHA256` | `examples/gate-f-candidate-baseline/config.json` 的 canonical JSON | 用仓库自身 `canonical_json_bytes` 实算 = `0662abe2…` | ✅ 一致 |
| 10 | `simple_cutout.py:45` `FROZEN_COMPARATOR_CONFIG_SHA256` | comparator config canonical | 实算 = `9ec194b3…` | ✅ 一致 |
| 11 | `model_candidate.py:41` `CONFIG_SHA256` | `examples/gate-f-model-candidate/config.json` canonical | 实算 = `e1a2e713…` | ✅ 一致 |
| 12 | `model_candidate.py:40` `ONTOLOGY_SHA256` | `registries/ontology-v0.1.yaml` | 磁盘 sha256 = `ea03fdf0…` | ✅ 一致 |
| 13 | `model_motion_draft.py:47` `CONFIG_SHA256` | motion config canonical | 实算 = `42a7effb…` | ✅ 一致 |
| 14 | `schemas/gate-f-model-candidate/v0.1/config.schema.json:23` const | 同 #12 | ✅ 一致 |
| 15 | `schemas/.../report.schema.json:19,23` const | 同 #11、#12 | ✅ 一致 |
| 16 | `examples/gate-f-model-candidate/config.json:12` | 同 #12 | ✅ 一致 |
| 17-23 | `gui/vendor/README.md:15-20,31-32`(7 条) | 各 vendored 文件 | 磁盘 sha256 全部逐条实算 | ✅ 全部一致 |
| 24-27 | `examples/gate-f-spike-smoke/README.md:15-18` + `run-spec.json` | 4 个 fixture 文件 | 磁盘 sha256 | ✅ 全部一致 |
| 28 | `tests/*.py` 中的 `2b9c10df…`(3 处) | frame-sequence 计算摘要(非文件) | 由 3 个通过的测试 + preflight 通过佐证 | ✅ |
| — | `model_profiles/hysts-*.json`、`docs/legal/*`、`examples/cir-minimal/*` | 外部权重/wheel/占位符 | 不在仓库内,无法磁盘验证(设计如此) | n/a |

**结论:workbench / motion / candidate 的生产代码、schema、examples、tests 中不存在任何内嵌的旧 entrypoint digest 期望。** motion 与 candidate 只通过 `identity["profile_sha256"]`(由 `_identity` 从磁盘 profile 实算)和 `profile_id` 字符串传递身份,没有硬编码入口摘要。F3 涟漪已收口。

---

## 5. 新缺陷列表

### N1 [中] 已校验的执行期 provenance 被丢弃,报告仍公布未加后缀的算法标识
- **file/line**:`spikes/gate_f_runner/model_worker.py:514`、`:518-593`(`finally: path.unlink(missing_ok=True)` 在 `:592-593`)、`:886-900`;`spikes/gate_f_runner/model_workbench.py:906`;`spikes/gate_f_runner/model_profiles/see-through-v3-nf4.json:167`
- **issue**:F3 新增的 `.entrypoint-attestation.json` 严格校验了 device policy id、`requested_cpu_offload`、三组件设备处置,以及 `psd_pixel_projection_algorithm_id == "source-visible-rgb-by-depth-mask-clean.v2.psd-postcorrect.v1"`(`model_worker.py:35`)。但 `_consume_entrypoint_attestation()` 返回 `None`、在 `finally` 中删除该文件,`run_model_worker()` 的返回字典(`:886-900`)不含任何 attestation 字段。于是 `model-result.json` / `workbench-report.json` 里**没有任何证据表明这些检查发生过**;而 workbench 发布的 `postprocess_algorithm` 取自 profile 的 `algorithm_id`(`…mask-clean.v2`,**不带 `.psd-postcorrect.v1` 后缀**),比实际执行的管线少描述了一整个 PSD 后校正步骤。
- **evidence**:`model_workbench.py:906` `"postprocess_algorithm": postprocess.get("algorithm_id")`;profile `:167` 值为 `"source-visible-rgb-by-depth-mask-clean.v2"`;`model_worker.py:35` 实际执行标识为 `f"{SOURCE_PRESERVE_ALGORITHM_ID}.psd-postcorrect.v1"`。F3 的目标是"provenance 归真",这一条与目标直接相悖。
- **建议**:把 attestation 的不可变摘要(`policy_id`、`execution_device`、三组件 `disposition`/`upstream_cuda_move_suppressed`、`psd_pixel_projection_algorithm_id`)作为 `entrypoint_attestation` 字段并入 worker result 与 workbench report 的 `model.identity`,并把 `postprocess_algorithm` 改为发布带后缀的实际算法标识(同步更新 report schema)。

### N2 [低] F2 预算比可达上界宽约 424 倍;已知的可信长度未在读取前用于拒绝
- **file/line**:`spikes/gate_f_runner/acceptance.py:389-393`、`:407-425`;`spikes/gate_f_runner/runtime.py:44-54`
- **issue**:(a) 交接文档称"逐产物**流式**比对(峰值内存恒定)",实现并非流式——`:416` `read_bounded_file` 在 `runtime.py:49` 是 `stream.read(maximum + 1)` 的**整体读入**;峰值随声明长度变化,不是常量。(b) 预算取 `2 × 32 MiB ≈ 64 MiB`,而 `verify_bundle` 无论如何只可能接受**唯一那个** purpose-created bundle,其实测总大小为 **158,172 字节**。既然每个产物最终都要与 `trusted_evidence[name]` 逐字节相等,正确长度是**先验已知**的,却从未在读取前用来拒绝声明长度。攻击者可以声明单个 frame 为 ~64 MiB 并让 `verify_bundle` 先分配 64 MiB 再失败;GUI `frame_bytes` 每帧一次调用可重复触发。
- **evidence**:`producer_byte_budget = 2 * _MAX_ARM_BUNDLE_OUTPUT_BYTES + Σ(非 arm 可信长度)`;实测 bundle 总字节 158,172 vs 预算 67,108,864。
- **建议**:在 `_read_verified_bundle_index` 的描述符循环里直接加 `if (digest, byte_length) != (sha256(trusted_evidence[name]).hexdigest(), len(trusted_evidence[name])): raise`。这会把峰值内存钉到 fixture 的真实大小(最大单产物 ~17 KB),并让全部超量声明在任何 I/O 之前被拒绝,严格强于当前的聚合预算。同时修正交接文档中"流式/恒定"的措辞。

### N3 [低] `_png_facts` 先解码后校验画布,且绕过 Pillow 12.1.0 钉版与解压炸弹护栏
- **file/line**:`spikes/gate_f_runner/model_workbench.py:320-333`(`:326` `image.load()` 在 `:327` 的尺寸检查**之前**),调用点 `:1008、1020、1024、1032、1040`
- **issue**:同文件的 `_rgba_pixels` (`:336-352`) 与 `model_worker._validated_png` (`:651-678`) 都是**先查尺寸再 `load()`** 的正确顺序,`_png_facts` 是唯一的例外;它还没有 `MAX_IMAGE_PIXELS` 夹紧或 `DecompressionBombWarning→error`(对比 `raster.py:471-477` 和 `acceptance.py:443` 都做了)。Pillow 默认只在 >2×89M 像素时抛错,89M–178M 区间仅告警并完整解码,即 ~715 MB RGBA。此外 `:321` 用 `from PIL import Image` 直接 import,绕过了 `raster._load_pillow()` 的 `PIL.__version__ != "12.1.0"` 钉版(`_neutral_fidelity:567`、`_rgb_mismatch_mask:554` 同样)。
- **evidence**:`_png_facts` 在 `450344c` 中是上下文行(未改动),但它位于 F1 触碰的文件、且在 `load_model_workbench_report()` 重算路径上对工作区中的模型输出直接生效——该路径不经过 worker 的 `_validated_png`。
- **建议**:把 `_png_facts` 改为接受期望画布并在 `image.load()` 前校验 `image.size`,改用 `_load_pillow()`,并按 `raster.py:471-477` 的写法夹紧 `MAX_IMAGE_PIXELS` / 升级 bomb 警告。

### N4 [低] 可见 alpha 阈值仍未统一:workbench 用 15,v4/candidate/profile 用 31
- **file/line**:`spikes/gate_f_runner/model_workbench.py:69` `MODEL_VISIBLE_ALPHA_THRESHOLD = 15`(用于 `:584、:586、:611`)vs `model_candidate.py:50` `SOURCE_VISIBLE_ALPHA_THRESHOLD = 31`、`model_entrypoints/see_through_v3_nf4_source_preserve_v4.py:26-27` `ALPHA_NOISE_FLOOR = 31`、`model_profiles/see-through-v3-nf4.json:168` `visible_alpha_threshold: 31`(由 `model_worker.py:634` 强制)
- **issue**:交接文档发现 23-25 声称"阈值统一为 31",但只统一了 candidate 侧。中性保真的分母与覆盖率判据仍用 15,而 v4 的 `_clean_alpha` 以 31 为噪声底(≤31 归零)、源 RGB 回填也以 `>31` 为界。源 alpha 落在 (15, 31] 的软边缘像素会被计入 `source_visible` 分母并要求被覆盖,但 v4 在该区间既不回填也可能把重建 alpha 清零 → `source_visible_coverage_ratio < 1.0` → `status: review_required` → 阻断 motion/candidate。方向是**过严**(fail-closed,非安全漏洞),但会在真实软边缘素材上造成非预期的链路阻断,且报告里 `alpha_threshold: 15` 与 profile 里 `visible_alpha_threshold: 31` 并列,读者无从判断哪个是权威。
- **建议**:把 `MODEL_VISIBLE_ALPHA_THRESHOLD` 改为从 profile 的 `postprocess.visible_alpha_threshold` 读取(而非独立字面量),或显式记录两个阈值的语义差异并在 report 里区分命名。

### N5 [低] entrypoint/profile 的 digest 绑定对行尾敏感,仓库无 `.gitattributes`
- **file/line**:仓库根(无 `.gitattributes`);`spikes/gate_f_runner/model_worker.py:164-166、180-182`
- **issue**:四个 entrypoint `.py` 与 profile `.json` 当前均为纯 LF(实测 CR 计数为 0),profile 声明的正是 LF 摘要。F3 移除了双 digest 容错并**新增**了第二个 digest 绑定文件,使这条链更脆:任何设置了 `core.autocrlf=true` 的 Windows 检出都会把这些文件转成 CRLF,`_validated_entrypoint` 立刻抛 `"model entrypoint digest mismatch"`,整条 model 路径不可用。交接文档 §4 P1-3 已记录该 Windows 环境"`git diff` 会对多数文件报 LF will be replaced by CRLF",说明这不是理论风险。
- **evidence**:`ls -a` 无 `.gitattributes`;`git config core.autocrlf` 未设置(本机);handoff:147。
- **建议**:加 `.gitattributes`,至少 `spikes/gate_f_runner/model_entrypoints/** -text` 与 `spikes/gate_f_runner/model_profiles/** -text`(或全仓库 `* text=auto eol=lf`)。

### N6 [低] profile bytes 与入口语义变了但 `profile_id` 未变,"v4" 现在指代两个不同的 attestation
- **file/line**:`spikes/gate_f_runner/model_profiles/see-through-v3-nf4.json:4`(`profile_id` 保持 `…source-preserve.v4`)
- **issue**:profile 文件自身摘要由 `03c2eba5…`(`a9be957`)变为 `d24de596…`(HEAD),入口脚本也从"仅源保留"扩展为"源保留 + NF4 设备策略 + PSD 后校正 + attestation"(新增行为,见 `see_through_v3_nf4_source_preserve_v4.py:208-271`)。`_identity` 用实算 `profile_sha256` 比对,所以旧 v4 运行会**硬失败**(fail-closed,无安全影响),但 `see-through.v3.nf4.1280.wsl2.source-preserve.v4` 这个 ID 现在无法唯一标识一套 attestation。相关地,`LEGACY_PROFILE_SHA256` / `LEGACY_SOURCE_PRESERVE_PROFILE_SHA256`(清单 #7/#8)对应的 profile 内容从未入库,无法用磁盘或 git 历史验证——这两条是 F3 之前就存在的不可验证断言。
- **建议**:要么把 profile_id 升到 `source-preserve.v5`(与 CLAUDE.md 的历史 profile 处理惯例一致),要么在 profile 里加显式的 `attestation_revision` 字段并在报告中发布;同时把 v2/v3 的历史 profile JSON 内容归档入库,让 `LEGACY_*_PROFILE_SHA256` 变成可验证的。

### N7 [低] GUI 单帧请求现在触发一次完整 bundle 验证
- **file/line**:`spikes/gate_f_runner/gui_server.py`(`frame_bytes` 改为 `verified_bundle_artifact_bytes(...)`);`spikes/gate_f_runner/acceptance.py:686-700`
- **issue**:原实现是 `read_bounded_file(directory / name, MAX_FRAME_BYTES)`(O(1));现在每帧请求都跑完整 `_verify_bundle`:78 次文件读 + 74 次 PNG 解码 + PSD 解析。安全性上这是改进(帧字节现在必须通过全量验证才外发),但把单请求成本放大了两个数量级。
- **evidence**:实测 30 ms/帧、整页 74 帧约 2.2 s(bundle 很小,故影响有限);`_purpose_created_arm_evidence` 的 `lru_cache` 只省掉重算部分,读取/比对/解码循环每次都重跑。
- **建议**:可接受现状(loopback-only 且实测在可用范围内);若要优化,让 `verified_bundle_artifact_bytes` 在同一进程内按 bundle 目录 mtime/inode 做一次性验证缓存,而不是每帧重跑。

### N8 [低] 验证器与生产者存在四处逐字复制,独立性弱于宣称且有漂移风险
- **file/line**:`spikes/gate_f_runner/acceptance.py:109-121`(`_purpose_created_source`) / `:124-136`(`_normalization_config`) / `:139-199`(`_arm_run_spec`) / `:29`(`_MAX_ARM_BUNDLE_OUTPUT_BYTES`) 分别复制自 `spikes/gate_f_runner/local_preflight.py:26-34 / 37-38 / 41-64 / 42`
- **issue**:F2/发现21 的"独立重算"其实是把生产者代码复制到验证器里。它能证明**字节可复现**,但证明不了算法独立性;更实际的风险是单边修改会让 `verify_bundle` 拒绝所有新产 bundle(fail-closed,且 preflight 测试会抓到,故风险有限)。`33554432` 在两处以独立字面量存在,尤其容易漂移。
- **建议**:把 `_purpose_created_source` / `_normalization_config` / run-spec 构造与 arm 输出上限提取到单一模块由两侧共享,并在文档里把这一步准确描述为"确定性字节可复现校验"而非"独立重算"。

---

## 6. 复核员搜查过的面(供判断覆盖度)

- **F1 面**:`__main__.py` 全文;`model_workbench.py` 的 `_publish_bytes` / `_canonical_source_png_bytes` / `_trusted_source_artifact` / `_normalization_artifact_path` / `_source_trust` / `_neutral_fidelity` / `build_model_workbench_report` / `load_model_workbench_report` / `run_normalized_model_workbench` / `run_uploaded_model_workbench`;失败清理路径;motion 与 candidate 的准入门;`gui_server.py` 全量 diff;F1 回归测试及其相邻正/负向用例。
- **F2 面**:`acceptance.py` 全文 + 完整 diff(含被删除的旧实现);`runtime.py` 的 `read_bounded_file` / `sha256_file` / `MAX_JSON_BYTES`;`contracts.py` 异常层级(确认 `SpecValidationError` 是 `ValueError`,不会逃逸 CLI 的 except);`local_preflight.py` 生产者侧上限;PNG/PSD/JSON 三类解码器的调用时序;`paired_experiment.arm_identity_from_report`;实测 bundle 大小与验证耗时。
- **F3 面**:profile JSON 全文;`model_worker.py` 全文;v4 入口脚本全文;`nf4_marigold_device_policy.py` 全文(含 attestation 的 fail-closed 路径);四个 entrypoint 文件的磁盘 sha256;git 全历史的 profile blob 摘要;`aedb9e25` 全仓库搜索;行尾/`.gitattributes`/`core.autocrlf`。
- **横向面**:全仓库 64-hex 常量清单(28 项,逐条核对);`f_usable` / `ballot` / `paired outcome` / `F-USABLE` / `moc3` 声明(全部显式否定,schema 用 `const: false` 钉死);固定状态措辞与 `GATE_F_NOT_EVALUATED` 配对;Pillow 钉版覆盖面;四条固定命令 + 两种解释器下的完整测试套件实跑。

**红线合规**:本次复核未修改/创建/删除任何仓库文件,未 commit;未建议生成或声称 ballot、paired outcome、`F-USABLE` 或 20 项 Gate F 结果;未触及 `.moc3`;所有成功状态措辞按 CLAUDE.md 固定值引用。
