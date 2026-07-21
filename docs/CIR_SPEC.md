# Canonical Intermediate Representation / `.oc2d` v0.2 候选规范

- **状态**：Gate F 前候选；v0.1 仅为可丢弃 spike IR
- **原则**：CIR 是权威项目；PSD 和预览缓冲是只读投影。

## 1. 范围

v0.2 只承诺最小可渲染能力：普通 alpha 栅格层、显式本体完备性、生成区域溯源、有界标量参数和简单 mesh-delta 绑定。物理、录制信号、复杂 cage/warp、多维插值和未被一致性样例执行的 deformer 暂不进入规范。

`.oc2d` 是 OneClick2D 原创格式，不是 Cubism 项目或 `.moc3`，也不表示兼容。

## 2. 坐标与媒体

- 图像/UV 原点：左上；X 向右，Y 向下；
- 位置：源画布浮点像素；UV：`[0,1]`；
- 左右：角色解剖学左右；屏幕方向必须写 `screen-left/right`；
- 持久图像：sRGB、straight alpha；蒙版：线性覆盖；
- 角度：degree；时间：second；
- 所有数值有限；单位和空间不可省略。

## 3. 标识和引用

所有非资源语义实体 ID 在项目内全局唯一，符合 `^[a-z][a-z0-9_.-]{2,127}$`，不使用数组位置或显示名作为身份；不同实体种类也不得碰撞。资源用 `sha256:<digest>` 独立命名。规范为每种引用声明允许的 target kind；语义验证拒绝重复、跨类碰撞、未解析、多重解析和 wrong-kind target。要求无环的图必须无环。

## 4. 根对象

最小项目包含：

- `format`、`format_version`；
- `project_id`、`revision_id`、`parent_revision_id`；
- `canvas`；
- `ontology_registry_version` 与 `ontology_completion`；
- `artifacts`、`layers`、`meshes`；
- `parameter_registry`（ID、版本、SHA-256）及 `parameters`、`bindings`；
- `reason_code_registry`（ID、版本、SHA-256）；
- `confidence_facts`、`generated_regions`、`provenance`；
- `user_overrides`；
- namespaced `extensions`。

`manifest.json` 不包含验证报告摘要，避免循环摘要。

## 5. 本体完备性

每个适用槽位都记录：

- stable slot ID；
- `PRESENT` / `NOT_APPLICABLE` / `LOW_CONFIDENCE`；
- instance IDs；
- confidence fact IDs；
- reason codes；
- evidence references。

静默缺失无效。

## 6. 层与生成区域

Layer 记录 stable ID、语义槽位、角色侧别、显示名、栅格资源、边界、可见性、opacity、normal blend、绘制顺序和可选 clipping。v0.2 只支持 normal alpha。

Generated region 记录：

- owner layer；
- generated mask；
- motion reveal envelope；
- feather width（pixel）；
- confidence fact；
- producer stage/model/config/seed；
- source/provenance ID；
- 允许的检查/修正策略。

生成覆盖不能在羽化容差外替换可见原始像素。

## 7. 最小几何 ABI

### 顶点

`oc2d.mesh.xyuv.f32le.v1`：每个顶点依序 4 个有限 float32 little-endian：源像素 `x, y`、归一化 `u, v`。

### 索引

紧密排列的 uint16le 或 uint32le 三元组，类型显式声明。禁止越界、非三角长度、退化和错误 winding。

### 变形 delta

`oc2d.delta.xy.f32le.v1`：与目标 mesh 顶点数严格匹配的有限 float32 `dx, dy`。

所有 payload 声明 byte length、element count、target mesh 和 SHA-256；读取前验证长度，禁止依赖宿主字节序。

## 8. 参数和绑定

项目通过包含 ID、版本和 SHA-256 的不可变参数注册表引用解析能力状态、单位、neutral、sign 和允许角色；引用不可解析或项目字段不一致时 fail closed。Gate F 前注册表允许 `candidate_mandatory`、`candidate_optional`、`research_optional`，不能映射成已批准的 mandatory/optional。Gate F 后发布新注册表版本，不原位改写候选语义。

语义验证要求 `minimum <= safe_minimum <= default <= safe_maximum <= maximum`，registry neutral 也在 safe range；每个最终 mandatory 参数 `manual_enabled=true`。一维 binding 样本值严格递增、唯一、位于参数域内并至少包含两个样本；引用解析到正确 parameter/mesh/artifact。多个同 parameter/mesh binding 如允许，按 registry 顺序再按 binding ID 组合。

v0.2 使用线性插值、clamp 外推。任何复杂变换、多维插值或非交换组合必须先定义公式和一致性样例。

## 9. 修订与覆盖

每次权威修改创建新 revision 和 payload digest。覆盖按操作区分 payload：mask add/erase 必须引用声明格式的 mask；draw-order 必须包含新 order/关系；range-reduce 必须包含新 min/max；raster-relink 必须引用 replacement raster；disable-optional 只作用于 optional target。

每个覆盖记录 parent revision、replay attempt 和 `clean/applied/conflict/rejected` 状态；`conflict/rejected` 必须有稳定 reason code，且阻止发布直到解决。重新生成显式重放兼容覆盖，禁止静默丢弃。

## 10. 溯源与置信

每个 producer 声明 `deterministic` 或 `model_backed`。模型 producer 必须同时给出不可变 model ID、weights SHA-256、rights-register record ID、配置摘要、输入摘要、provider/precision；确定性 producer 明确无模型，不得携带部分模型字段。大整数种子使用 20 位零填充十进制字符串，语义范围限定 `00000000000000000000`–`18446744073709551615`。

根 `confidence_facts` 中每项包含全局唯一 ID、正确类型 target、score 或 `unavailable`、校准 dataset/method/version、threshold band、evidence artifact IDs 和 provenance ID。Ontology 和 generated region 只引用这些事实；所有引用必须唯一解析。没有置信不等于通过。

## 11. 必须不变量

1. 严格 JSON 且所有引用/摘要解析；
2. 本体槽位完备；
3. 图层/绑定图满足声明的无环和稳定顺序；
4. 中性复合在容差内保护原作；
5. 所有网格、delta 和参数轨迹有限有效；
6. 生成像素可追溯；
7. 项目无需模型权重/推理即可渲染；
8. 权威修改使旧验证、确认和导出失效；
9. v0.2 支持范围外的强制能力安全失败。

## 12. 契约权威

JSON Schema 负责 wire shape；规范负责语义；跨语言一致性套件负责图/数值/渲染不变量。语言 DTO 由契约生成并检查漂移，禁止手写第二套权威类型。
