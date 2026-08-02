# 修复轮对抗复核 · Opus5 一路(原文存档)

> 复核对象:工作树未提交修复(基线 `ee953de`,N1/N2/N3/N4/N5/N8 修复包 A+B 之后)。2026-08-02 (UTC)。
> 只读复核;工作树未被触碰。以下为 Opus5 复核员输出原文(通知转义已还原)。

---

# 对抗性复核 · 工作树未提交修复(基线 `ee953de`)

**实跑门(scratchpad 副本,`TMPDIR` 已指向已解析路径):** 完整套件 **Ran 219, OK (skipped=16)**;`validate_docs.py` 通过(38 MD / 39 JSON);`smoke` `status=succeeded`;`preflight` `LOCAL_TECHNICAL_PREFLIGHT_PASS` + `GATE_F_NOT_EVALUATED`。工作树未被触碰,未 commit。

---

## 1. N1 — **CONFIRMED**

**闭环证据**

- `spikes/gate_f_runner/model_worker.py:449-455` `_invoke_wsl` 返回 `tuple[CompletedProcess, Mapping|None]`;`:526-527` 仅在 `returncode == 0` 时消费 attestation;`:946` `run_model_worker` 解包;`:947-949` `returncode != 0` 立即抛错,故进入 `:978` 时 attestation 必为已校验值。
- `:978` `"entrypoint_attestation": _entrypoint_attestation_dict(entrypoint_attestation)` 并入 worker result。
- `:529-596` `_validated_entrypoint_attestation_summary` **字段封闭性完整**:顶层 `set(value) != {6 键}`(:531-540);`set(components) != set(expected_dispositions)`(:552);每个组件 `set(component) != {4 键}`(:556-561)。返回 `MappingProxyType`(:576-586, :601-607)且 `storage/hooks` 转 `tuple` → 不可变摘要。
- `spikes/gate_f_runner/model_workbench.py:974` `_identity` 对 `result["entrypoint_attestation"]` **完整重跑同一验证器**(`_entrypoint_attestation_dict` → `_validated_entrypoint_attestation_summary`),不是信任 worker 自述。
- `:996` `postprocess_algorithm` 改发布 `PSD_PIXEL_PROJECTION_ALGORITHM_ID`。实测值 = `source-visible-rgb-by-depth-mask-clean.v2.psd-postcorrect.v1`(probe 输出)。

**伪造/注入绕过测试(13 组变异,全部 fail-closed)**

| 变异 | 结果 |
|---|---|
| 顶层加键 / 组件加键 / 多一个组件 | `MISMATCH`, `model_used=False`, `identity.entrypoint_attestation=None` |
| `vae.storage_devices=["cuda:0"]` / `unet.upstream_cuda_move_suppressed=False` | `MISMATCH` |
| `psd_projection_verified=1`(int 冒充 True)/ `requested_cpu_offload=1` | `MISMATCH`(`is not True` 生效) |
| `entrypoint_attestation` = `None` / `[]` / `{}` | `MISMATCH` |
| `storage_devices` 未排序 | `MISMATCH` |
| 字段缺失 | `MISSING` |
| 未改动 | `model_used=True`,`reason_codes=[]` |

**legacy 分支拒收完备**:`model_workbench.py:1047-1048` 在 `_identity` 之前拦截;实测 legacy v2 携带 attestation → `StageContractError: legacy model workbench result has unexpected attestation`;未知 profile_id 携带 attestation 同样被拦。

**差分模糊测试(HEAD vs 工作树,261 例变异)**:`accept/reject divergences=0`,`head_accept=38 / wt_accept=38`。抽取到共享校验器**未弱化任何断言**(唯一差异 `isinstance(storage, (list, tuple))` 放宽,JSON 只产 list,无影响)。

**须记录的固有局限(非缺陷)**:38/261 的接受率说明——通过校验后仅剩 3 个 device 列表 + 1 个 bool 是自由量,其余全是编译期常量。因此报告里的 `entrypoint_attestation` 是"worker 声称固定 device policy 成立"的**可逐字复现的自述**,不像 `_source_trust` 那样有像素级密码绑定。写得动 `model-result.json` 的人可以照抄一份合法摘要。这符合 N1 的要求("并入不可变摘要"),但不应被描述为执行期的密码学证明。

---

## 2. N2 — **CONFIRMED**

- `spikes/gate_f_runner/acceptance.py:296-305`:描述符循环内,首个合法条目触发 `trusted_evidence = dict(_purpose_created_bundle_evidence())` + `_trusted_evidence_descriptors(...)`,随后 `trusted_descriptors.get(name) != descriptor → raise`。
- **确实在所有产物 I/O 之前**。我用比现有测试更强的探针验证:同时 patch `builtins.open` **和** `Path.open`(而非仅 `acceptance.read_bounded_file`),把某条目声明为 512 MiB 后跑 `verify_bundle`:

```
files opened inside bundle dir: ['.../run.probe-preflight.bundle/bundle-index.json']
```

  bundle 目录内**只有 index 被打开**,80 个产物零读取。实测 bundle 真实总量 147,619 字节。
- 重复名有独立防线(`:288` `name in descriptors`),缺项由 `:306-307` 精确集合比对兜底。
- 单条 512 MiB(`:293`)与聚合预算(`:314-318`)保留为纵深。
- `_read_matching_bundle_artifact:332-350` 顺序仍为 读 → 长度 → sha256 → 逐字节相等 → 才 `return`;解码器全部在其后。

**`lru_cache` 跨调用状态风险:无。** `_purpose_created_bundle_evidence`(`:165-190`)与 `_purpose_created_arm_evidence`(`:112-162`)均为无参、纯函数(输入=仓库内 example config + 代码),返回 `tuple[(str, bytes)]` 不可变;调用点每次 `dict(...)` 复制。全仓库 tests 中无任何对这两个函数或其输入的 patch/`cache_clear`(grep 零命中),不存在缓存投毒面。

---

## 3. N3 — **CONFIRMED**

- `model_workbench.py:333-366`:先校验 `expected_canvas` 形状(`:340-344`),再 `_load_pillow()`(`:345`,钉版 12.1.0 见 `raster.py:109-110`),夹紧 `MAX_IMAGE_PIXELS = 宽×高`(`:348`),`DecompressionBombWarning → error`(`:350`),**`image.size != expected_canvas` 在 `image.load()` 之前**(`:352-359`),`finally` 恢复全局(`:366`)。
- **全部 5 个调用点都传了期望画布**:`:1107`(src_img)、`:1124`(reconstruction)、`:1132`(src_img/src_head 循环)、`:1143`(semantic)、`:1155`(depth,画布取自已被钉死为 1280×1280 的 semantic facts,故 depth 同样被钉死)。旧代码"解码后再比"的三处后置检查已全部删除。
- **无漏改的直接 PIL import**:`grep 'from PIL import|import PIL' spikes/gate_f_runner/model_workbench.py` 零命中。`_rgb_mismatch_mask`(`:574-580`)与 `_neutral_fidelity`(`:615-617`)改为先 `_load_pillow()` 再 `importlib.import_module("PIL.ImageChops"/"PIL.ImageStat")`,版本门先于导入生效。
- 探针实测:错模式 PNG → 拒绝且 `PngImageFile.load` **未被调用**;4000×4000 vs 1280 画布 → 拒绝且 `load` 未被调用;`(True, True)` / `(-1, 5)` / 3 元组画布 → `model workbench PNG canvas profile is invalid`;调用后 `PIL.Image.MAX_IMAGE_PIXELS` 正确恢复为 89478485。

*残留(不在 N3 范围、本次未回归)*:`model_worker.py:738`、`model_candidate.py`、`model_motion_draft.py` 仍用裸 `from PIL import ...`,钉版覆盖面仍不统一。

---

## 4. N4 — **CONFIRMED**

- 读取路径:`model_workbench.py:953-967` 顺序为 `_load_profile()` → `result["profile_sha256"] == sha256_bytes(profile_bytes)`(`:961`,把磁盘 profile 与 result 声明绑定)→ `_validated_entrypoint(profile)`(入口 + device policy 双 digest)→ `_validated_inference` → `_validated_postprocess`。**profile 未验证时不存在读取路径**:任一步失败即 `StageContractError`,不产报告,无 fail-open。
- `:1001` `int(postprocess["visible_alpha_threshold"])` + `:1002` `MODEL_PROFILE_ALPHA_THRESHOLD_SOURCE`。而 `model_worker.py:708-718` `_validated_postprocess` **硬钉 `visible_alpha_threshold != 31 → raise`**,故 active v4 只能是 31。
- legacy v2/v3 分支(`:918-920`、`:948-950`)返回 `15` + `legacy-workbench-constant.v1`,**identity 字典与 HEAD 逐字段一致**(`postprocess_algorithm` 仍为 `not_applied` / `source-visible-rgb-by-depth.v1`),**无 v2/v3 追溯改写**。
- `_neutral_fidelity:607-614` 对 `alpha_threshold`(bool 排除、0–255)与 `alpha_threshold_source`(仅两个白名单常量)做入参校验。
- **组合自相矛盾不可能**:v4 → (31, profile-source);v2/v3 → (15, legacy-source)。15 配 profile-source 需要 profile 写 15,但那会被 `_validated_postprocess` 直接拒;31 配 legacy-source 无代码路径。测试 `test_active_profile_uses_profile_alpha_threshold_for_soft_source_edges`(31)与两个 legacy 测试(15)分别锁定。

---

## 5. N5 — **CONFIRMED**

`.gitattributes:4-9` 对照上轮 §4 的 28 项清单,逐条核对**是否为 raw-byte 绑定**:

| §4 清单项 | 绑定方式 | 覆盖 | `git check-attr text` |
|---|---|---|---|
| #1 #2 v4 入口 + device policy `.py` | `sha256_bytes(read_bounded_file(...))`,raw | `model_entrypoints/**` | `unset` ✅ |
| #4 #5 legacy 两入口 `.py` | `sha256_file`,raw | 同上 | `unset` ✅ |
| profile JSON 自身摘要 | `sha256_bytes(exact)`,raw(`model_worker.py:113-118`) | `model_profiles/**` | `unset` ✅ |
| #3 #6 `dependencies_sha256` | raw(`model_worker.py:940`) | `model_profiles/**`(文件在该目录内) | `unset` ✅ |
| `requirements-pillow-12.1.0-win-py314.txt` | wheel hash 文件 | `spikes/gate_f_runner/requirements-*.txt` | `unset` ✅ |
| #12 ontology | `sha256_file(ONTOLOGY_PATH)`,raw(`model_candidate.py:132`) | `registries/**` | `unset` ✅ |
| #17-23 vendored 资产 | README 记录的 raw sha256 | `gui/vendor/**` | `unset` ✅ |
| #24-27 smoke fixtures | raw | `examples/gate-f-spike-smoke/**` | `unset` ✅ |
| #9 #10 #11 #13 冻结 config | **`sha256_bytes(canonical_json_bytes(parsed))`**(`candidate_baseline.py:65`、`simple_cutout.py:66`、`model_candidate.py:105`、`model_motion_draft.py:138`)→ **EOL 免疫** | 不需要 | `unspecified`,正确 |
| #14-16 schema/example 内嵌 const | 同 #9-13 派生 | 不需要 | — |
| #28 frame-sequence 计算摘要 | 非文件 | — | — |

另核实:`gui/index.html` **无 SRI**,`gui/app.js|styles.css|live_preview.mjs` 未被任何 digest 绑定;`gui_server.py:214`、`runner.py`、`runtime.py:643`、`model_motion_draft.py:1211/1245/1274` 的 `sha256_file` 全部作用于运行期工作区产物,与仓库检出无关。**未发现遗漏的 raw-digest 绑定路径。**

---

## 6. N8 — **CONFIRMED(字节级等价已实跑证明)**

`spikes/gate_f_runner/purpose_created.py:16/25/42/57` 提供 `MAX_ARM_BUNDLE_OUTPUT_BYTES` / `purpose_created_source` / `normalization_config` / `arm_run_spec`;`acceptance.py:20-25` 与 `local_preflight.py:12-16` 以别名导入,四处逐字复制与 `33554432` 双字面量全部消除(`grep struct|zlib` 在两个消费者中已零命中)。`_ROOT` 仍解析到同一仓库根(`parents[2]`,三个模块同目录)。

**差分实跑**(HEAD 副本 vs 工作树副本,同一进程内分别导入):

```
RESULT: ALL BYTE-EQUIVALENT       (24 组比对全 OK)
acc vs lp cross-check in worktree: source True / norm True / specs all identical True
```

覆盖:`purpose_created_source()`、`normalization_config()`、`MAX_ARM_BUNDLE_OUTPUT_BYTES`、candidate/comparator × 4 组 arm_config 的 run-spec、未知 arm 的异常类型与消息(`StageContractError: unknown preflight arm`)、`$schema` 根路径(归一化后一致)。旁证:`preflight` 全绿即证明生产者与验证器仍逐字节可复现。

---

## 7. 新缺陷列表

### [中] D1 — 报告契约变更未升 `format_version`,历史已发布报告全部无法复验

- **file/line**:`spikes/gate_f_runner/model_workbench.py:1277`(`"format_version": "0.3.0"`,与 HEAD 相同,未变);变更点 `:672`(新增 `alpha_threshold_source`)、`:997`(新增 `identity.entrypoint_attestation`)、`:996`(`postprocess_algorithm` 取值改变);校验闸 `:1377-1378`。
- **issue**:`load_model_workbench_report` 用 `persisted != report` 做全等比对。本次修复给报告新增了两个字段并改了一个已发布值,但 `format_version` 仍是 `0.3.0` —— 两种不同形状的报告自称同一版本。后果是任何在本次修复之前发布的 `workbench-report.json` 现在**硬失败**,且报错文案 `persisted model workbench report does not match validated evidence` 会被读成"证据被篡改",而实际原因是格式变更。
- **evidence**(实跑):
  ```
  report format_version = 0.3.0
  reload of pre-change v4 report:        RAISED StageContractError: persisted model workbench report does not match validated evidence
  reload of pre-change LEGACY v2 report: RAISED StageContractError: persisted model workbench report does not match validated evidence
  ```
  legacy v2 这一条尤其要注意:v2 的 `identity` 本身**没有**被追溯改写(§4 已确认),但 `quality.neutral_fidelity` 新增的 `alpha_threshold_source` 让历史 v2/v3 报告一样炸掉,与 CLAUDE.md"历史 v2/v3 结果继续按各自原 profile/入口摘要验证"的意图相抵。此外 `gui_server.py:159-160`、`:170-171` 对 `StageContractError` 一律 `continue`,历史 run 会从 GUI 列表**静默消失**。
- **建议**:把报告 `format_version` 升到 `0.4.0`,并让 `load_model_workbench_report` 对 `persisted["format_version"]` 与当前版本不一致的情况给出一个专门的、区别于"证据不匹配"的错误(或按版本迁移/拒绝);同步在 handoff 里记录"本次修复使既有已发布 workbench 报告需重新生成"。

### [低] D2 — `quality.reason_codes` 与 `quality.neutral_fidelity.reason_codes` 现在是同一个 list 对象

- **file/line**:`model_workbench.py:1253`(`quality_reasons = [...]`)、`:1256`(`neutral_fidelity["reason_codes"] = quality_reasons`)、`:1307`(`"reason_codes": quality_reasons`)
- **issue**:HEAD 是两次独立 `list(source_trust["reason_codes"])`,现在两处共享同一可变对象。任何下游就地修改一处会静默污染另一处。
- **evidence**(实跑):
  ```
  quality.reason_codes IS neutral_fidelity.reason_codes -> True
  after append to quality.reason_codes, neutral_fidelity.reason_codes = ['MUTATED_VIA_ALIAS']
  ```
  当前生产消费者只读(`gui_server` 走 `copy.deepcopy`),故暂无实际影响。
- **建议**:两处各写 `list(quality_reasons)`。

### [低] D3 — `PIL.Image.MAX_IMAGE_PIXELS` 全局改写新增到两个 GUI 请求线程可达的函数

- **file/line**:`model_workbench.py:346/348/366`(`_png_facts`)、`:618/620/708`(`_neutral_fidelity`);触发路径 `gui_server.py:521`(`class GuiServer(ThreadingHTTPServer)`)、`:158`/`:191` → `_model_report` → `load_model_workbench_report` → 上述两函数;并发对手是后台 workbench 工作线程(`gui_server.py:93`)及 `raster.py:471-537` 的同类改写。
- **issue**:save/restore 交错时,后完成的线程会把自己保存的(可能已被别人压低的)值写回,全局有被永久钉在 `1_638_400` 的窗口;`acceptance.py:367-368` 的 `_verify_frame_png` 只开 bomb 警告转错、不设自己的 `MAX_IMAGE_PIXELS`,会因此对合法大图误报。方向 fail-closed,且 `raster.py` 早有同一模式(**非本次新引入的模式**),但本次把它扩到了两个请求线程直达的函数上。
- **建议**:把三处统一封装成一个带进程级 `threading.Lock` 的上下文管理器,或改用 `Image.open` 后立刻显式尺寸判定、彻底不碰全局。

### [低] D4 — `_rgb_mismatch_mask` 新增的 `image_chops` 形参是死参数

- **file/line**:`model_workbench.py:574`(`def _rgb_mismatch_mask(difference, image_chops=None)`)vs 唯一调用点 `:657`(`_rgb_mismatch_mask(difference)`,未传)
- **issue**:`_neutral_fidelity` 已经解析好 `image_chops`(`:616`)却不传,函数内每次重新 `_load_pillow()` + `import_module`。参数纯装饰,读者会误以为已注入。
- **建议**:调用点传 `image_chops`,或删掉该形参。

### [低] D5 — `_neutral_fidelity` 默认参数是 legacy 15 / `legacy-workbench-constant.v1`

- **file/line**:`model_workbench.py:604-605`
- **issue**:唯一生产调用点(`:1246-1251`)显式传参,当前正确;但默认值会让任何忘记传参的新调用点把 active v4 运行**静默标成 15 + legacy 来源**,正是 N4 想消除的那种"报告里两个阈值并列、读者无从判断权威"的状态。
- **建议**:去掉默认值,改为必填关键字参数。

### [低] D6 — 聚合字节预算在生产路径已不可达,其回归测试靠 mock 才能进入

- **file/line**:`acceptance.py:314-318`;`tests/test_gate_f_acceptance.py:245-262`(`patch("...acceptance._trusted_evidence_descriptors", return_value=matching_descriptors)`)
- **issue**:精确核对(`:301`)已经先于预算拒绝一切超量声明,预算分支只能靠把 `_trusted_evidence_descriptors` 替换成回显篡改索引的假值才能触达。保留纵深本身合理,但该测试现在验证的是一条只在 mock 下存在的路径,容易让人误读为"预算仍在生效"。
- **建议**:给该用例改名/加注释(例如 `..._when_trusted_descriptors_are_unavailable`),明确它验证的是纵深分支而非生产路径。

### [低] D7 — 交接文档与代码不同步

- **file/line**:`docs/handoff/GATE_F_AUDIT_REMEDIATION_HANDOFF.md:81`、`:205`
- **issue**:(a) `:81` 仍写 F2 是"逐产物**流式**比对(峰值内存恒定)",而 `:201` 的新增附录自己指出该措辞与实现不符 —— 同一文档自相矛盾,N2 建议的"修正措辞"未落地;(b) `:205` 仍把 N1–N8 整体列为"下一轮待办",但同一变更集已经关闭了 N1/N2/N3/N4/N5/N8。
- **建议**:改写 `:81` 为"产物粒度顺序处理:声明描述符先与可信重算证据精确核对,再逐产物整体读入并逐字节比对";在 `:205` 标注 N1–N5、N8 已闭环,N6(决策项)/N7(接受现状)保持开放。

### [低] D8 — 非 active profile 一律报 "legacy ... unexpected attestation"

- **file/line**:`model_workbench.py:1047-1048`
- **issue**:条件是 `result.get("profile_id") != PROFILE_ID`,任意未知 profile_id 携带 attestation 都会被冠以 "legacy" 文案。实测:`profile_id="totally.unknown.v9"` → `legacy model workbench result has unexpected attestation`。fail-closed,仅文案误导。
- **建议**:改为 "non-active model workbench result has unexpected attestation"。

---

## 8. 搜查过但未发现问题的面

- **绕过面**:worker result → workbench 的 attestation 重验(13 组变异 + 261 例差分模糊,零绕过);legacy/未知 profile 携带 attestation;`build_model_workbench_report` 的 `required` / `allowed` 键集判定(缺键 + 多键组合均拒);`_consume_entrypoint_attestation` 的文件层 8 键封闭与 `finally: unlink`;`verify_bundle` 的 I/O 时序(`builtins.open` + `Path.open` 双 spy)。
- **fail-open / 断言弱化**:HEAD vs 工作树的 attestation 校验器差分(0 分歧);`_identity` 由 `.get()` 改为 `_validated_inference`/`_validated_postprocess`(**收紧**,不再允许 `None` 落入 identity);`_validated_entrypoint` 单值 digest 比较未被触碰;`_read_matching_bundle_artifact` 的 长度→digest→逐字节 顺序未变;`min(maximum, expected_length)` 上界未放宽。
- **v2/v3 追溯改写**:两个 legacy identity 字典与 HEAD 逐字段比对一致(`postprocess_algorithm` 未加后缀、`entrypoint_attestation` 键不存在、阈值仍 15);legacy 分支不读 profile、不受 v4 改动影响。唯一副作用是 D1 的报告形状。
- **测试 fixture 伪造运行**:`write_model_fixture` 注入的 attestation 由**生产校验器**判定,配套负向用例(missing / tampered)存在;`_invoke_wsl` 的 6 处 stub 全部改成返回二元组,`returncode=1` 的用例返回 `(mock, None)`;无测试 patch `_purpose_created_*` 或清缓存(grep 零命中),lru_cache 无投毒面;219 项在纯净副本中实跑通过。
- **`.gitattributes`**:`git check-attr text` 对 8 条 raw-digest 路径全部返回 `unset`,对 canonical-JSON 绑定路径返回 `unspecified`(正确);已确认 `gui/index.html` 无 SRI、`gui/*.js|css` 无 digest 绑定。
- **红线**:未生成或建议生成 ballot / paired outcome / `F-USABLE` / 20 项 Gate F 结果;未触及 `.moc3`;`LOCAL_TECHNICAL_PREFLIGHT_PASS`、`LOCAL_WORKBENCH_COMPLETED`、`LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED` 与 `GATE_F_NOT_EVALUATED` 的配对在代码与实跑输出中均未改变;N6/N7 未作为新发现重复上报。
