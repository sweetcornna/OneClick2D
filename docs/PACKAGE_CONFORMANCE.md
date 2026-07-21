# `.oc2d` 包一致性规范

## 1. 归档布局

```text
manifest.json
artifacts/<sha256>.<ext>
reports/validation.json
provenance/run-manifest.json
package-index.json
thumbnails/preview.png      # 可选、非权威
```

ZIP 成员使用 `/`。禁止绝对路径、`..`、盘符、反斜杠歧义、符号/硬链接、重复规范路径和大小写冲突。入口数、单成员/总解压大小、压缩比和嵌套深度必须有限。

## 2. 严格 JSON

- UTF-8；拒绝非法 UTF-8、重复键、NaN、Infinity；
- 限制深度、数组/对象成员数及字节数；
- 摘要使用 RFC 8785 canonical JSON；
- 仅允许 I-JSON 可互操作有限数；
- 超过 `2^53-1` 的整数使用固定宽度字符串。

## 3. 非循环摘要域

1. `manifest.json` 是权威项目内容，不含报告 hash；
2. `project_payload_sha256` = 规范化 `manifest.json` 字节的 SHA-256；
3. `validation.json` 绑定该 payload digest；语义验证要求 finding instance 唯一，ack 只能匹配当前报告中同 code+instance 的 `review` finding 且摘要/策略一致；`blocking` 不能被确认，blocking finding 或 blocked export 必须使报告 `blocked`；
4. `run-manifest.json` 的 project revision/payload 必须与 manifest 一致；model-backed stage 必须解析不可变 model/weights/rights，原因码必须解析到包内绑定的 registry snapshot；
5. `package-index.json` 记录除自身外每个最终成员的路径、类型、长度、角色及 SHA-256，包括 manifest/report/provenance/registry snapshots/artifacts；
6. 认证下载记录绑定最终 archive SHA-256。

最终 `.oc2d` archive digest 和 PSD digest 存在归档外的 release record，避免自引用；无批准签名时，只声明传输/存储损坏检测，不声明第三方真实性。

## 4. 发布算法

- 从一个不可变 revision 构建所有成员；
- 对成员做类型、大小、摘要、schema 和语义验证；
- 写临时 archive，固定成员顺序和规范时间策略；
- 独立读取器重新打开、重算摘要并渲染中性/参数样例；
- 在 `.oc2d` 归档之外创建符合 `schemas/release/v0.1/dual-output-release.schema.json` 的 versioned immutable dual-output release record，绑定 account/project/revision、project payload digest、最终 `.oc2d`/PSD 的 media type、byte length、SHA-256 和各自 verifier/tool/config/pass evidence；
- 只有两个制品都通过且绑定同一 payload 时原子发布；每个下载授权引用 release ID 和精确 artifact digest，失败或不匹配时 fail closed。

## 5. 一致性样例

采用 CIR v0.2 前必须提供真实 `examples/conformance/minimal.oc2d`：

- 已知 canonical member bytes；
- 已知 payload/report/index/archive hashes；
- 中性及参数极值预期渲染；
- 写入器、独立读取器和独立渲染器共同使用；
- 编辑产生新 revision、旧验证失效；
- 迁移在副本上执行。

## 6. 必须负例

- 路径穿越、绝对/盘符/反斜杠、大小写碰撞；
- 重复 ZIP/JSON 键；
- symlink、未知成员、隐藏 active content；
- member/count/ratio/depth bomb；
- 错误长度、类型、摘要和 ABI stride；
- 非有限数、不安全整数、未解析引用、图环；
- validation 绑定旧 payload；
- package index 缺少/多出成员；
- 不支持的 required feature/major version；
- archive 被篡改。

## 7. 版本与迁移

读取器公布明确支持范围并对未知强制语义 fail closed。旧的 `oneclick2d.cir` + `0.1.0` 是未采纳 disposable spike，读取器必须明确拒绝为 unsupported spike，不把它计入 predecessor/current 支持；只有另行提供迁移时才可转换。迁移为 `old package → new package + migration report`，只在副本执行，不覆盖用户原文件。G1 由 D-016 决定精确 predecessor/current 和迁移 fixtures，G4 决定支持期限、deprecation、reader 和服务结束方案。

## 8. 工具阶段

当前 `scripts/validate_docs.py` 只做立项 lint，不能称为包一致性验证。正式验证器必须使用固定版本的标准兼容 JSON Schema 工具、语义图/有限数检查、真实 ZIP/hash 验证、独立 reopen/render 和篡改负例。
